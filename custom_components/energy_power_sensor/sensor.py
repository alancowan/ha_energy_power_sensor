"""Sensor platform for the Energy & Power Sensor integration."""

from __future__ import annotations

from datetime import timedelta
import logging
import math
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .calculation import EnergyAccumulator
from .const import (
    BASE_UNITS,
    CONF_INPUT_UNITS,
    CONF_MAX_POWER,
    CONF_OUTLIER_THRESHOLD,
    CONF_OUTPUT_UNIT,
    CONF_SENSOR_TYPE,
    CONF_SUBTRACT_ENTITIES,
    CONF_SUM_ENTITIES,
    CONF_UPDATE_INTERVAL,
    CONVERTERS,
    DEFAULT_OUTPUT_UNITS,
    TYPE_ENERGY,
    UNIT_AUTO,
)

_LOGGER = logging.getLogger(__name__)

PRECISION = 6


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor from a config entry."""
    async_add_entities([EnergyPowerSensor(entry)])


class EnergyPowerSensor(SensorEntity, RestoreEntity):
    """Sensor that adds and subtracts energy or power sensors."""

    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        options = entry.options
        self._type: str = entry.data[CONF_SENSOR_TYPE]
        self._is_energy = self._type == TYPE_ENERGY
        self._sum: list[str] = list(options.get(CONF_SUM_ENTITIES, []))
        self._subtract: list[str] = list(options.get(CONF_SUBTRACT_ENTITIES, []))
        self._input_units: dict[str, str] = dict(options.get(CONF_INPUT_UNITS, {}))
        self._interval = int(options.get(CONF_UPDATE_INTERVAL, 0) or 0)
        self._max_power: float | None = options.get(CONF_MAX_POWER)
        self._outlier_threshold: float | None = options.get(CONF_OUTLIER_THRESHOLD)
        self._converter = CONVERTERS[self._type]
        self._base_unit = BASE_UNITS[self._type]
        self._accumulator: EnergyAccumulator | None = None
        self._ignored_count = 0
        self._warned_units: set[str] = set()

        self._attr_name = entry.title
        self._attr_unique_id = entry.entry_id
        self._attr_native_unit_of_measurement = options.get(
            CONF_OUTPUT_UNIT, DEFAULT_OUTPUT_UNITS[self._type]
        )
        if self._is_energy:
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_state_class = SensorStateClass.TOTAL
        else:
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def _all_entities(self) -> list[str]:
        return self._sum + self._subtract

    async def async_added_to_hass(self) -> None:
        """Restore state and start listening."""
        await super().async_added_to_hass()

        if self._is_energy:
            restored = None
            if (extra := await self.async_get_last_extra_data()) is not None:
                restored = extra.as_dict().get("energy")
            signs = {eid: 1 for eid in self._sum}
            signs.update({eid: -1 for eid in self._subtract})
            self._accumulator = EnergyAccumulator(signs, self._max_power, restored)

        if self._interval > 0:
            self.async_on_remove(
                async_track_time_interval(
                    self.hass,
                    self._async_interval_update,
                    timedelta(seconds=self._interval),
                )
            )
        else:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self._all_entities, self._async_state_changed
                )
            )

        self._recalculate()
        # Inputs from other integrations may not be loaded yet during startup.
        self.async_on_remove(async_at_started(self.hass, self._async_started))

    @callback
    def _async_started(self, _hass: HomeAssistant) -> None:
        self._recalculate()
        self.async_write_ha_state()

    @callback
    def _async_state_changed(self, _event: Event[EventStateChangedData]) -> None:
        self._recalculate()
        self.async_write_ha_state()

    @callback
    def _async_interval_update(self, _now: Any) -> None:
        self._recalculate()
        self.async_write_ha_state()

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        """Persist the energy offsets so outlier corrections survive restarts."""
        if self._accumulator is None:
            return None
        return RestoredExtraData({"energy": self._accumulator.as_dict()})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes."""
        if self._accumulator is not None:
            acc = self._accumulator
            return {
                "outliers_detected": acc.outlier_count,
                "last_outlier_entity": acc.last_outlier_entity,
                "last_outlier_time": (
                    acc.last_outlier_time.isoformat() if acc.last_outlier_time else None
                ),
            }
        return {"outliers_ignored": self._ignored_count}

    def _read(self, entity_id: str) -> float | None:
        """Return an input's value in the base unit, or None if unusable."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        if not math.isfinite(value):
            return None

        unit = self._input_units.get(entity_id, UNIT_AUTO)
        if unit == UNIT_AUTO:
            unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if unit not in self._converter.VALID_UNITS:
            if entity_id not in self._warned_units:
                self._warned_units.add(entity_id)
                _LOGGER.warning(
                    "%s: cannot use %s, its unit %r is not a valid %s unit. "
                    "Set the unit for this input in the integration options",
                    self.entity_id,
                    entity_id,
                    unit,
                    self._type,
                )
            return None
        return self._converter.convert(value, unit, self._base_unit)

    def _to_output(self, value: float) -> float:
        return self._converter.convert(
            value, self._base_unit, self._attr_native_unit_of_measurement
        )

    @callback
    def _recalculate(self) -> None:
        readings = {eid: self._read(eid) for eid in self._all_entities}
        if self._accumulator is not None:
            self._update_energy(readings)
        else:
            self._update_power(readings)

    def _update_energy(self, readings: dict[str, float | None]) -> None:
        assert self._accumulator is not None
        total = self._accumulator.update(readings, dt_util.utcnow())
        if total is not None:
            self._attr_native_value = round(self._to_output(total), PRECISION)

    def _update_power(self, readings: dict[str, float | None]) -> None:
        if any(value is None for value in readings.values()):
            self._attr_available = False
            return
        self._attr_available = True

        total = sum(readings[eid] for eid in self._sum) - sum(
            readings[eid] for eid in self._subtract
        )
        value = self._to_output(total)
        if self._outlier_threshold is not None and abs(value) > self._outlier_threshold:
            # Keep the previous value rather than publishing the outlier.
            self._ignored_count += 1
            _LOGGER.debug(
                "%s: ignoring outlier %s %s (limit %s)",
                self.entity_id,
                value,
                self._attr_native_unit_of_measurement,
                self._outlier_threshold,
            )
            return
        self._attr_native_value = round(value, PRECISION)

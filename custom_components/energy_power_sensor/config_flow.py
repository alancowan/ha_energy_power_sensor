"""Config flow for the Energy & Power Sensor integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, CONF_NAME, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_IGNORE_UNAVAILABLE,
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
    DOMAIN,
    MAX_UPDATE_INTERVAL,
    SENSOR_TYPES,
    TYPE_ENERGY,
    UNIT_AUTO,
    valid_units,
)

ENTITY_LIST_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", multiple=True)
)


def _inputs_schema(sensor_type: str) -> vol.Schema:
    schema: dict[vol.Marker, Any] = {
        vol.Optional(CONF_SUM_ENTITIES, default=[]): ENTITY_LIST_SELECTOR,
        vol.Optional(CONF_SUBTRACT_ENTITIES, default=[]): ENTITY_LIST_SELECTOR,
        vol.Required(
            CONF_OUTPUT_UNIT, default=DEFAULT_OUTPUT_UNITS[sensor_type]
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=valid_units(sensor_type),
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required(CONF_UPDATE_INTERVAL, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=MAX_UPDATE_INTERVAL,
                step=1,
                unit_of_measurement="s",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
    }
    if sensor_type == TYPE_ENERGY:
        schema[vol.Optional(CONF_MAX_POWER)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                step="any",
                unit_of_measurement=UnitOfPower.WATT,
                mode=selector.NumberSelectorMode.BOX,
            )
        )
    else:
        schema[vol.Optional(CONF_OUTLIER_THRESHOLD)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, step="any", mode=selector.NumberSelectorMode.BOX
            )
        )
        schema[vol.Optional(CONF_IGNORE_UNAVAILABLE, default=False)] = (
            selector.BooleanSelector()
        )
    return vol.Schema(schema)


def _validate_inputs(user_input: dict[str, Any]) -> dict[str, str]:
    sum_entities = user_input.get(CONF_SUM_ENTITIES, [])
    subtract_entities = user_input.get(CONF_SUBTRACT_ENTITIES, [])
    if not sum_entities and not subtract_entities:
        return {"base": "no_entities"}
    if set(sum_entities) & set(subtract_entities):
        return {"base": "duplicate_entities"}
    return {}


def _clean_inputs(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalise number fields from the form."""
    data = dict(user_input)
    data[CONF_UPDATE_INTERVAL] = int(data.get(CONF_UPDATE_INTERVAL) or 0)
    for key in (CONF_MAX_POWER, CONF_OUTLIER_THRESHOLD):
        if data.get(key) in (None, "", 0):
            data.pop(key, None)
    return data


def _entities(options: dict[str, Any]) -> list[str]:
    return list(options.get(CONF_SUM_ENTITIES, [])) + list(
        options.get(CONF_SUBTRACT_ENTITIES, [])
    )


def _units_schema(
    sensor_type: str, entities: list[str], current: dict[str, str]
) -> vol.Schema:
    options = [selector.SelectOptionDict(value=UNIT_AUTO, label="Auto (sensor's unit)")]
    options += [
        selector.SelectOptionDict(value=unit, label=unit)
        for unit in valid_units(sensor_type)
    ]
    unit_selector = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options, mode=selector.SelectSelectorMode.DROPDOWN
        )
    )
    return vol.Schema(
        {
            vol.Required(eid, default=current.get(eid, UNIT_AUTO)): unit_selector
            for eid in entities
        }
    )


def _units_placeholders(hass: HomeAssistant, entities: list[str]) -> dict[str, str]:
    lines = []
    for eid in entities:
        state = hass.states.get(eid)
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if state else None
        lines.append(f"- `{eid}`: {unit or 'no unit'}")
    return {"detected_units": "\n".join(lines)}


def _validate_units(
    hass: HomeAssistant, sensor_type: str, user_input: dict[str, str]
) -> dict[str, str]:
    """Inputs set to auto must report a unit we can convert."""
    errors = {}
    valid = CONVERTERS[sensor_type].VALID_UNITS
    for eid, unit in user_input.items():
        if unit != UNIT_AUTO:
            continue
        state = hass.states.get(eid)
        detected = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if state else None
        if detected not in valid:
            errors[eid] = "unit_required"
    return errors


class EnergyPowerSensorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._name = ""
        self._sensor_type = TYPE_ENERGY
        self._options: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return EnergyPowerSensorOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the name and sensor type."""
        if user_input is not None:
            self._name = user_input[CONF_NAME]
            self._sensor_type = user_input[CONF_SENSOR_TYPE]
            return await self.async_step_inputs()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): selector.TextSelector(),
                    vol.Required(
                        CONF_SENSOR_TYPE, default=TYPE_ENERGY
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=SENSOR_TYPES,
                            translation_key=CONF_SENSOR_TYPE,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_inputs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose input sensors and output settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_inputs(user_input)
            if not errors:
                self._options = _clean_inputs(user_input)
                return await self.async_step_units()

        schema = _inputs_schema(self._sensor_type)
        if user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(
            step_id=f"inputs_{self._sensor_type}", data_schema=schema, errors=errors
        )

    # The form step id differs per type so each gets its own help text.
    async_step_inputs_energy = async_step_inputs
    async_step_inputs_power = async_step_inputs

    async def async_step_units(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the unit of each input sensor."""
        entities = _entities(self._options)
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_units(self.hass, self._sensor_type, user_input)
            if not errors:
                self._options[CONF_INPUT_UNITS] = user_input
                return self.async_create_entry(
                    title=self._name,
                    data={CONF_SENSOR_TYPE: self._sensor_type},
                    options=self._options,
                )

        return self.async_show_form(
            step_id="units",
            data_schema=_units_schema(self._sensor_type, entities, user_input or {}),
            description_placeholders=_units_placeholders(self.hass, entities),
            errors=errors,
        )


class EnergyPowerSensorOptionsFlow(OptionsFlow):
    """Handle options."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}

    @property
    def _sensor_type(self) -> str:
        return self.config_entry.data[CONF_SENSOR_TYPE]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit input sensors and output settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_inputs(user_input)
            if not errors:
                self._options = _clean_inputs(user_input)
                return await self.async_step_units()

        schema = self.add_suggested_values_to_schema(
            _inputs_schema(self._sensor_type),
            user_input if user_input is not None else self.config_entry.options,
        )
        return self.async_show_form(
            step_id=f"inputs_{self._sensor_type}", data_schema=schema, errors=errors
        )

    async_step_inputs_energy = async_step_init
    async_step_inputs_power = async_step_init

    def _update_registry_unit(self) -> None:
        """Apply a changed output unit.

        Home Assistant pins a sensor's display unit in the entity registry when the
        entity is first created, so a new output unit must be written there too.
        """
        new_unit = self._options[CONF_OUTPUT_UNIT]
        if new_unit == self.config_entry.options.get(CONF_OUTPUT_UNIT):
            return
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            SENSOR_DOMAIN, DOMAIN, self.config_entry.entry_id
        )
        if entity_id is not None:
            registry.async_update_entity_options(
                entity_id,
                f"{SENSOR_DOMAIN}.private",
                {"suggested_unit_of_measurement": new_unit},
            )

    async def async_step_units(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the unit of each input sensor."""
        entities = _entities(self._options)
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_units(self.hass, self._sensor_type, user_input)
            if not errors:
                self._options[CONF_INPUT_UNITS] = user_input
                self._update_registry_unit()
                return self.async_create_entry(data=self._options)

        current = user_input or self.config_entry.options.get(CONF_INPUT_UNITS, {})
        return self.async_show_form(
            step_id="units",
            data_schema=_units_schema(self._sensor_type, entities, current),
            description_placeholders=_units_placeholders(self.hass, entities),
            errors=errors,
        )

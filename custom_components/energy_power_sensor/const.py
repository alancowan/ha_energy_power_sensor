"""Constants for the Energy & Power Sensor integration."""

from __future__ import annotations

from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.util.unit_conversion import EnergyConverter, PowerConverter

DOMAIN = "energy_power_sensor"

CONF_SENSOR_TYPE = "sensor_type"
CONF_SUM_ENTITIES = "sum_entities"
CONF_SUBTRACT_ENTITIES = "subtract_entities"
CONF_INPUT_UNITS = "input_units"
CONF_OUTPUT_UNIT = "output_unit"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_MAX_POWER = "max_power"
CONF_OUTLIER_THRESHOLD = "outlier_threshold"

TYPE_ENERGY = "energy"
TYPE_POWER = "power"
SENSOR_TYPES = [TYPE_ENERGY, TYPE_POWER]

# Input unit option meaning "use the unit_of_measurement reported by the sensor".
UNIT_AUTO = "auto"

MAX_UPDATE_INTERVAL = 3600

# Changes are judged over at least this many seconds, so readings that arrive in
# quick bursts are not mistaken for an implausibly high power.
MIN_OUTLIER_WINDOW = 60

# Internal base units: all maths is done in these, then converted for output.
BASE_UNITS = {
    TYPE_ENERGY: UnitOfEnergy.WATT_HOUR,
    TYPE_POWER: UnitOfPower.WATT,
}
CONVERTERS = {
    TYPE_ENERGY: EnergyConverter,
    TYPE_POWER: PowerConverter,
}
DEFAULT_OUTPUT_UNITS = {
    TYPE_ENERGY: UnitOfEnergy.KILO_WATT_HOUR,
    TYPE_POWER: UnitOfPower.WATT,
}


def valid_units(sensor_type: str) -> list[str]:
    """Return the selectable units for a sensor type."""
    return sorted(str(unit) for unit in CONVERTERS[sensor_type].VALID_UNITS if unit)

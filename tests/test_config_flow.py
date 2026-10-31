"""Tests for the config and options flow."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.energy_power_sensor.const import (
    CONF_INPUT_UNITS,
    CONF_MAX_POWER,
    CONF_OUTPUT_UNIT,
    CONF_SENSOR_TYPE,
    CONF_SUBTRACT_ENTITIES,
    CONF_SUM_ENTITIES,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)


async def test_create_energy_sensor(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.a", "1", {ATTR_UNIT_OF_MEASUREMENT: "kWh"})
    hass.states.async_set("sensor.b", "1")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "House energy", CONF_SENSOR_TYPE: "energy"}
    )
    assert result["step_id"] == "inputs_energy"

    # Nothing selected.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_OUTPUT_UNIT: "kWh", CONF_UPDATE_INTERVAL: 0},
    )
    assert result["errors"] == {"base": "no_entities"}

    # Same sensor in both lists.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SUM_ENTITIES: ["sensor.a"],
            CONF_SUBTRACT_ENTITIES: ["sensor.a"],
            CONF_OUTPUT_UNIT: "kWh",
            CONF_UPDATE_INTERVAL: 0,
        },
    )
    assert result["errors"] == {"base": "duplicate_entities"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SUM_ENTITIES: ["sensor.a"],
            CONF_SUBTRACT_ENTITIES: ["sensor.b"],
            CONF_OUTPUT_UNIT: "kWh",
            CONF_UPDATE_INTERVAL: 60.0,
            CONF_MAX_POWER: 10000,
        },
    )
    assert result["step_id"] == "units"

    # sensor.b has no unit, so "auto" is rejected for it.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"sensor.a": "auto", "sensor.b": "auto"}
    )
    assert result["errors"] == {"sensor.b": "unit_required"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"sensor.a": "auto", "sensor.b": "Wh"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "House energy"
    assert result["data"] == {CONF_SENSOR_TYPE: "energy"}
    assert result["options"] == {
        CONF_SUM_ENTITIES: ["sensor.a"],
        CONF_SUBTRACT_ENTITIES: ["sensor.b"],
        CONF_OUTPUT_UNIT: "kWh",
        CONF_UPDATE_INTERVAL: 60,
        CONF_MAX_POWER: 10000,
        CONF_INPUT_UNITS: {"sensor.a": "auto", "sensor.b": "Wh"},
    }
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.house_energy").state) == 0.999


async def test_options_flow(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.a", "100", {ATTR_UNIT_OF_MEASUREMENT: "W"})
    hass.states.async_set("sensor.b", "50", {ATTR_UNIT_OF_MEASUREMENT: "W"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Total",
        data={CONF_SENSOR_TYPE: "power"},
        options={
            CONF_SUM_ENTITIES: ["sensor.a"],
            CONF_SUBTRACT_ENTITIES: [],
            CONF_OUTPUT_UNIT: "W",
            CONF_UPDATE_INTERVAL: 0,
            CONF_INPUT_UNITS: {"sensor.a": "auto"},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.total").state == "100.0"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "inputs_power"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SUM_ENTITIES: ["sensor.a"],
            CONF_SUBTRACT_ENTITIES: ["sensor.b"],
            CONF_OUTPUT_UNIT: "kW",
            CONF_UPDATE_INTERVAL: 0,
        },
    )
    assert result["step_id"] == "units"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"sensor.a": "auto", "sensor.b": "auto"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    state = hass.states.get("sensor.total")
    assert state.state == "0.05"
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "kW"

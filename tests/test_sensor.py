"""Tests for the sensor entity."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from custom_components.energy_power_sensor.const import (
    CONF_IGNORE_UNAVAILABLE,
    CONF_INPUT_UNITS,
    CONF_MAX_POWER,
    CONF_OUTLIER_THRESHOLD,
    CONF_OUTPUT_UNIT,
    CONF_SENSOR_TYPE,
    CONF_SUBTRACT_ENTITIES,
    CONF_SUM_ENTITIES,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)


def _set(hass: HomeAssistant, entity_id: str, value, unit: str | None) -> None:
    attrs = {ATTR_UNIT_OF_MEASUREMENT: unit} if unit else {}
    hass.states.async_set(entity_id, str(value), attrs)


async def _setup(hass: HomeAssistant, sensor_type: str, **options) -> str:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Total",
        data={CONF_SENSOR_TYPE: sensor_type},
        options=options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return "sensor.total"


async def test_power_sum_subtract_with_unit_conversion(hass: HomeAssistant) -> None:
    _set(hass, "sensor.solar", 2, "kW")
    _set(hass, "sensor.grid", 500, "W")
    _set(hass, "sensor.battery", 300, None)
    eid = await _setup(
        hass,
        "power",
        **{
            CONF_SUM_ENTITIES: ["sensor.solar", "sensor.grid"],
            CONF_SUBTRACT_ENTITIES: ["sensor.battery"],
            CONF_INPUT_UNITS: {
                "sensor.solar": "auto",
                "sensor.grid": "auto",
                "sensor.battery": "W",
            },
            CONF_OUTPUT_UNIT: "kW",
            CONF_UPDATE_INTERVAL: 0,
        },
    )
    state = hass.states.get(eid)
    assert float(state.state) == 2.2
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "kW"
    assert state.attributes["device_class"] == "power"

    _set(hass, "sensor.grid", 1500, "W")
    await hass.async_block_till_done()
    assert float(hass.states.get(eid).state) == 3.2


async def test_power_unavailable_input(hass: HomeAssistant) -> None:
    _set(hass, "sensor.a", 100, "W")
    _set(hass, "sensor.b", 100, "W")
    eid = await _setup(
        hass,
        "power",
        **{
            CONF_SUM_ENTITIES: ["sensor.a", "sensor.b"],
            CONF_INPUT_UNITS: {},
            CONF_OUTPUT_UNIT: "W",
        },
    )
    assert hass.states.get(eid).state == "200.0"
    hass.states.async_set("sensor.b", STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == STATE_UNAVAILABLE


async def test_power_outlier_threshold_keeps_previous(hass: HomeAssistant) -> None:
    _set(hass, "sensor.a", 100, "W")
    eid = await _setup(
        hass,
        "power",
        **{
            CONF_SUM_ENTITIES: ["sensor.a"],
            CONF_OUTPUT_UNIT: "W",
            CONF_OUTLIER_THRESHOLD: 1000,
        },
    )
    _set(hass, "sensor.a", 99999, "W")
    await hass.async_block_till_done()
    state = hass.states.get(eid)
    assert state.state == "100.0"
    assert state.attributes["outliers_ignored"] == 1

    _set(hass, "sensor.a", 400, "W")
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "400.0"


async def test_interval_mode(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _set(hass, "sensor.a", 100, "W")
    eid = await _setup(
        hass,
        "power",
        **{
            CONF_SUM_ENTITIES: ["sensor.a"],
            CONF_OUTPUT_UNIT: "W",
            CONF_UPDATE_INTERVAL: 30,
        },
    )
    assert hass.states.get(eid).state == "100.0"

    _set(hass, "sensor.a", 200, "W")
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "100.0"

    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "200.0"


async def test_energy_outlier_and_units(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _set(hass, "sensor.meter1", 10, "kWh")
    _set(hass, "sensor.meter2", 2000, "Wh")
    eid = await _setup(
        hass,
        "energy",
        **{
            CONF_SUM_ENTITIES: ["sensor.meter1", "sensor.meter2"],
            CONF_INPUT_UNITS: {"sensor.meter1": "auto", "sensor.meter2": "auto"},
            CONF_OUTPUT_UNIT: "kWh",
            CONF_MAX_POWER: 5000,
        },
    )
    state = hass.states.get(eid)
    assert float(state.state) == 12.0
    assert state.attributes["device_class"] == "energy"
    assert state.attributes["state_class"] == "total"

    # Meter 1 replaced: reads 0.
    freezer.tick(timedelta(minutes=1))
    _set(hass, "sensor.meter1", 0, "kWh")
    await hass.async_block_till_done()
    state = hass.states.get(eid)
    assert float(state.state) == 12.0
    assert state.attributes["outliers_detected"] == 1
    assert state.attributes["last_outlier_entity"] == "sensor.meter1"

    freezer.tick(timedelta(minutes=10))
    _set(hass, "sensor.meter1", 0.5, "kWh")
    await hass.async_block_till_done()
    assert float(hass.states.get(eid).state) == 12.5


async def test_energy_restores_offsets(hass: HomeAssistant) -> None:
    """A stored offset from before a restart is applied to new readings."""
    last_change = (dt_util.utcnow() - timedelta(hours=2)).isoformat()
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("sensor.total", "12.0"),
                {
                    "energy": {
                        "base": 0.0,
                        "inputs": {
                            "sensor.meter1": {
                                "sign": 1,
                                "last_raw": 0.0,
                                "last_change": last_change,
                                "offset": 10000.0,
                            },
                        },
                        "outlier_count": 1,
                    }
                },
            )
        ],
    )
    # 4 kWh in 2 hours = 2 kW: within the limit.
    _set(hass, "sensor.meter1", 4, "kWh")
    eid = await _setup(
        hass,
        "energy",
        **{
            CONF_SUM_ENTITIES: ["sensor.meter1"],
            CONF_INPUT_UNITS: {"sensor.meter1": "auto"},
            CONF_OUTPUT_UNIT: "kWh",
            CONF_MAX_POWER: 5000,
        },
    )
    state = hass.states.get(eid)
    assert float(state.state) == 14.0
    assert state.attributes["outliers_detected"] == 1


async def test_energy_wrong_unit_input_ignored(hass: HomeAssistant) -> None:
    _set(hass, "sensor.meter1", 5, "kWh")
    _set(hass, "sensor.bad", 5, "W")
    eid = await _setup(
        hass,
        "energy",
        **{
            CONF_SUM_ENTITIES: ["sensor.meter1"],
            CONF_SUBTRACT_ENTITIES: ["sensor.bad"],
            CONF_INPUT_UNITS: {"sensor.meter1": "auto", "sensor.bad": "auto"},
            CONF_OUTPUT_UNIT: "Wh",
        },
    )
    assert float(hass.states.get(eid).state) == 5000.0


async def test_power_ignore_unavailable(hass: HomeAssistant) -> None:
    _set(hass, "sensor.a", 100, "W")
    _set(hass, "sensor.b", 50, "W")
    eid = await _setup(
        hass,
        "power",
        **{
            CONF_SUM_ENTITIES: ["sensor.a"],
            CONF_SUBTRACT_ENTITIES: ["sensor.b"],
            CONF_OUTPUT_UNIT: "W",
            CONF_IGNORE_UNAVAILABLE: True,
        },
    )
    assert hass.states.get(eid).state == "50.0"

    hass.states.async_set("sensor.b", STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    state = hass.states.get(eid)
    assert state.state == "100.0"
    assert state.attributes["unavailable_entities"] == ["sensor.b"]

    hass.states.async_set("sensor.a", STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == STATE_UNAVAILABLE


async def test_missing_entity_raises_repair_issue(hass: HomeAssistant) -> None:
    _set(hass, "sensor.a", 1, "kWh")
    _set(hass, "sensor.b", 2, "kWh")
    eid = await _setup(
        hass,
        "energy",
        **{
            CONF_SUM_ENTITIES: ["sensor.a", "sensor.b"],
            CONF_OUTPUT_UNIT: "kWh",
        },
    )
    entry_id = hass.config_entries.async_entries("energy_power_sensor")[0].entry_id
    issue_id = f"missing_entities_{entry_id}"
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, issue_id) is None

    # Entity deleted: energy total keeps its last value and an issue is raised.
    hass.states.async_remove("sensor.b")
    await hass.async_block_till_done()
    state = hass.states.get(eid)
    assert float(state.state) == 3.0
    assert state.attributes["unavailable_entities"] == ["sensor.b"]
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert "sensor.b" in issue.translation_placeholders["entities"]

    # Entity comes back: the issue clears.
    _set(hass, "sensor.b", 2.5, "kWh")
    await hass.async_block_till_done()
    assert float(hass.states.get(eid).state) == 3.5
    assert issues.async_get_issue(DOMAIN, issue_id) is None


async def test_unavailable_entity_does_not_raise_issue(hass: HomeAssistant) -> None:
    _set(hass, "sensor.a", 100, "W")
    await _setup(
        hass,
        "power",
        **{CONF_SUM_ENTITIES: ["sensor.a"], CONF_OUTPUT_UNIT: "W"},
    )
    hass.states.async_set("sensor.a", STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    assert not ir.async_get(hass).issues

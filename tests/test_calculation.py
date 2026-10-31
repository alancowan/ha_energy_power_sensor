"""Tests for the energy outlier logic."""

from datetime import datetime, timedelta, timezone

from custom_components.energy_power_sensor.calculation import EnergyAccumulator

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_sum_and_subtract():
    acc = EnergyAccumulator({"a": 1, "b": 1, "c": -1}, None)
    assert acc.update({"a": 1000, "b": 500, "c": 200}, at(0)) == 1300
    assert acc.update({"a": 1100, "b": 500, "c": 250}, at(5)) == 1350


def test_no_value_until_first_reading():
    acc = EnergyAccumulator({"a": 1}, 5000)
    assert acc.update({"a": None}, at(0)) is None
    assert acc.update({"a": 100}, at(1)) == 100


def test_meter_reset_is_absorbed_and_counting_continues():
    acc = EnergyAccumulator({"a": 1, "b": 1}, 5000)
    assert acc.update({"a": 10_000, "b": 2_000}, at(0)) == 12_000
    # Meter 'a' replaced: drops to 0. Output holds instead of dropping 10 kWh.
    assert acc.update({"a": 0, "b": 2_000}, at(1)) == 12_000
    assert acc.outlier_count == 1
    assert acc.last_outlier_entity == "a"
    # Normal counting resumes from the new meter: +50 Wh in 1 minute = 3 kW.
    assert acc.update({"a": 50, "b": 2_000}, at(2)) == 12_050


def test_spike_up_is_absorbed():
    acc = EnergyAccumulator({"a": 1}, 5000)
    acc.update({"a": 1000}, at(0))
    assert acc.update({"a": 1_000_000}, at(1)) == 1000
    assert acc.update({"a": 1_000_010}, at(2)) == 1010


def test_large_change_after_long_downtime_is_accepted():
    """30 kWh in 10 hours = 3 kW average, within a 5 kW limit."""
    acc = EnergyAccumulator({"a": 1}, 5000)
    acc.update({"a": 0}, at(0))
    assert acc.update({"a": 30_000}, at(600)) == 30_000
    assert acc.outlier_count == 0


def test_power_measured_since_input_last_changed():
    """An unchanged reading doesn't reset the timing window."""
    acc = EnergyAccumulator({"a": 1}, 1000)
    acc.update({"a": 0}, at(0))
    for minute in range(1, 60):
        acc.update({"a": 0}, at(minute))
    # 900 Wh over 60 minutes = 900 W: fine.
    assert acc.update({"a": 900}, at(60)) == 900
    assert acc.outlier_count == 0


def test_short_bursts_use_minimum_window():
    """Two readings 1 s apart: 10 Wh judged over 60 s = 600 W, below 1 kW."""
    acc = EnergyAccumulator({"a": 1}, 1000)
    acc.update({"a": 0}, at(0))
    acc.update({"a": 10}, at(10))
    assert acc.update({"a": 20}, at(10) + timedelta(seconds=1)) == 20
    assert acc.outlier_count == 0


def test_no_limit_means_no_outliers():
    acc = EnergyAccumulator({"a": 1}, None)
    acc.update({"a": 10_000}, at(0))
    assert acc.update({"a": 0}, at(1)) == 0


def test_unavailable_input_keeps_last_contribution():
    acc = EnergyAccumulator({"a": 1, "b": 1}, 5000)
    acc.update({"a": 1000, "b": 1000}, at(0))
    assert acc.update({"a": 1100, "b": None}, at(5)) == 2100
    # 'b' comes back after 60 min having used 1 kWh (1 kW avg): counted.
    assert acc.update({"a": 1100, "b": 2000}, at(60)) == 3100


def test_input_added_later_starts_from_zero():
    acc = EnergyAccumulator({"a": 1, "b": 1}, None)
    acc.update({"a": 1000, "b": None}, at(0))
    assert acc.update({"a": 1000, "b": 50_000}, at(1)) == 1000
    assert acc.update({"a": 1000, "b": 50_100}, at(2)) == 1100


def test_restore_round_trip():
    acc = EnergyAccumulator({"a": 1}, 5000)
    acc.update({"a": 10_000}, at(0))
    acc.update({"a": 0}, at(1))  # outlier
    stored = acc.as_dict()

    restored = EnergyAccumulator({"a": 1}, 5000, stored)
    # After a 2 hour restart gap, 4 kWh is 2 kW: accepted.
    assert restored.update({"a": 4000}, at(121)) == 14_000
    assert restored.outlier_count == 1


def test_restore_with_removed_input_keeps_total():
    acc = EnergyAccumulator({"a": 1, "b": 1}, None)
    acc.update({"a": 1000, "b": 2000}, at(0))
    restored = EnergyAccumulator({"a": 1}, None, acc.as_dict())
    assert restored.update({"a": 1100}, at(1)) == 3100


def test_restore_with_input_added_starts_from_zero():
    acc = EnergyAccumulator({"a": 1}, None)
    acc.update({"a": 1000}, at(0))
    restored = EnergyAccumulator({"a": 1, "b": -1}, None, acc.as_dict())
    assert restored.update({"a": 1000, "b": 9999}, at(1)) == 1000
    assert restored.update({"a": 1000, "b": 10_099}, at(2)) == 900


def test_restore_with_input_moved_to_subtract():
    acc = EnergyAccumulator({"a": 1, "b": 1}, None)
    acc.update({"a": 1000, "b": 500}, at(0))
    restored = EnergyAccumulator({"a": 1, "b": -1}, None, acc.as_dict())
    assert restored.update({"a": 1000, "b": 500}, at(1)) == 1500
    assert restored.update({"a": 1000, "b": 600}, at(2)) == 1400


def test_restore_ignores_garbage():
    acc = EnergyAccumulator({"a": 1}, None, {"inputs": {"a": {"sign": "x"}}})
    assert acc.update({"a": 5}, at(0)) == 5

"""Calculation logic for the Energy & Power Sensor integration.

All values handled here are in base units (Wh for energy, W for power).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from homeassistant.util import dt as dt_util

from .const import MIN_OUTLIER_WINDOW

_LOGGER = logging.getLogger(__name__)


@dataclass
class _InputTracker:
    """Running state for one energy input."""

    sign: int
    last_raw: float | None = None
    last_change: datetime | None = None
    offset: float = 0.0

    @property
    def contribution(self) -> float:
        """Return this input's signed, outlier-corrected contribution."""
        if self.last_raw is None:
            return 0.0
        return self.sign * (self.last_raw + self.offset)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sign": self.sign,
            "last_raw": self.last_raw,
            "last_change": self.last_change.isoformat() if self.last_change else None,
            "offset": self.offset,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> _InputTracker:
        last_raw = data.get("last_raw")
        last_change = data.get("last_change")
        parsed = dt_util.parse_datetime(last_change) if last_change else None
        return cls(
            sign=int(data["sign"]),
            last_raw=None if last_raw is None else float(last_raw),
            last_change=dt_util.as_utc(parsed) if parsed else None,
            offset=float(data.get("offset", 0.0)),
        )


class EnergyAccumulator:
    """Sum/subtract energy inputs while absorbing implausible jumps.

    Each input keeps a stored offset. When an input changes faster than the
    group's maximum power allows (e.g. a meter was replaced and reset to zero,
    or reported a bogus spike), the whole jump is added to that input's offset,
    so the output carries on from where it was on the next cycle.

    The check is based on power (change / time since the input last changed),
    so a large change after Home Assistant has been offline for a while is
    accepted as long as the average power over that period is plausible.
    """

    def __init__(
        self,
        signs: dict[str, int],
        max_power: float | None,
        restored: dict[str, Any] | None = None,
    ) -> None:
        self._max_power = max_power or None
        self._trackers = {eid: _InputTracker(sign) for eid, sign in signs.items()}
        self._base = 0.0
        self._initialized = False
        self.outlier_count = 0
        self.last_outlier_entity: str | None = None
        self.last_outlier_time: datetime | None = None
        if restored:
            self._restore(restored)

    def _restore(self, data: dict[str, Any]) -> None:
        try:
            base = float(data.get("base", 0.0))
            stored = {
                eid: _InputTracker.from_dict(item)
                for eid, item in data.get("inputs", {}).items()
            }
            outlier_count = int(data.get("outlier_count", 0))
            last_outlier_time = data.get("last_outlier_time")
        except (KeyError, TypeError, ValueError):
            _LOGGER.warning("Ignoring invalid stored energy state: %s", data)
            return

        self._base = base
        self._initialized = True
        self.outlier_count = outlier_count
        self.last_outlier_entity = data.get("last_outlier_entity")
        parsed = dt_util.parse_datetime(last_outlier_time) if last_outlier_time else None
        self.last_outlier_time = dt_util.as_utc(parsed) if parsed else None

        for eid, old in stored.items():
            new = self._trackers.get(eid)
            if new is None:
                # Input was removed: keep what it contributed so the total doesn't drop.
                self._base += old.contribution
            elif new.sign != old.sign:
                # Input moved between the sum and subtract lists: freeze its old
                # contribution and let it count from zero in its new role.
                self._base += old.contribution
                if old.last_raw is not None:
                    new.last_raw = old.last_raw
                    new.last_change = old.last_change
                    new.offset = -old.last_raw
            else:
                self._trackers[eid] = old

    def as_dict(self) -> dict[str, Any]:
        """Return state to persist across restarts."""
        return {
            "base": self._base,
            "inputs": {eid: t.as_dict() for eid, t in self._trackers.items()},
            "outlier_count": self.outlier_count,
            "last_outlier_entity": self.last_outlier_entity,
            "last_outlier_time": (
                self.last_outlier_time.isoformat() if self.last_outlier_time else None
            ),
        }

    def update(self, readings: dict[str, float | None], now: datetime) -> float | None:
        """Process new readings (Wh, None if unavailable) and return the total in Wh.

        Unavailable inputs keep contributing their last known value.
        Returns None until at least one input has ever reported a value.
        """
        for eid, tracker in self._trackers.items():
            raw = readings.get(eid)
            if raw is None:
                continue

            if tracker.last_raw is None:
                # First reading. Inputs present when the sensor is first created
                # count with their full value; inputs added later start from zero
                # so they don't cause a jump in the total.
                tracker.offset = -raw if self._initialized else 0.0
                tracker.last_raw = raw
                tracker.last_change = now
                continue

            delta = raw - tracker.last_raw
            if delta == 0:
                continue

            if self._is_outlier(delta, tracker.last_change, now):
                tracker.offset -= delta
                self.outlier_count += 1
                self.last_outlier_entity = eid
                self.last_outlier_time = now
                _LOGGER.warning(
                    "Ignoring jump of %.3f Wh in %s (from %.3f Wh to %.3f Wh): "
                    "exceeds maximum power of %s W",
                    delta,
                    eid,
                    tracker.last_raw,
                    raw,
                    self._max_power,
                )

            tracker.last_raw = raw
            tracker.last_change = now

        if not self._initialized:
            if all(t.last_raw is None for t in self._trackers.values()):
                return None
            self._initialized = True

        return self._base + sum(t.contribution for t in self._trackers.values())

    def _is_outlier(
        self, delta: float, last_change: datetime | None, now: datetime
    ) -> bool:
        if self._max_power is None:
            return False
        elapsed = MIN_OUTLIER_WINDOW
        if last_change is not None:
            elapsed = max(elapsed, (now - last_change).total_seconds())
        return abs(delta) / (elapsed / 3600) > self._max_power

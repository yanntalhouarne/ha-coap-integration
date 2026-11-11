"""The plant's side: when it will need water, from how fast its soil dries (as the fleet
manager's forecast, manager/hacoap_manager/fleet.py water_forecast)."""

from __future__ import annotations

DAY = 86400
WATERING_RISE = 5   # % of soil up between two readings: it was watered (only the drying since counts)
MIN_DRYING = 0.5    # % a day: drying slower than this (or not at all) gives no forecast


def _slope(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope of (time, value) points, per second."""
    n = len(points)
    mean_t = sum(t for t, _ in points) / n
    mean_v = sum(v for _, v in points) / n
    spread = sum((t - mean_t) ** 2 for t, _ in points)
    return sum((t - mean_t) * (v - mean_v) for t, v in points) / spread if spread else None


def needs_water_at(points: list[tuple[float, float]], target: float | None, now: float) -> float | None:
    """When the soil goes under 'target' % at the pace it dried since its last watering: a time
    ('now' when it's under already), or None: no target, not drying, or under 3 readings over 6
    hours since the last watering. 'points': (time, soil %), oldest first."""
    if not target or not points:
        return None
    start = 0
    for i in range(1, len(points)):
        if points[i][1] > points[i - 1][1] + WATERING_RISE:
            start = i
    run = points[start:]
    soil = run[-1][1]
    if soil < target:
        return now
    if len(run) < 3 or run[-1][0] - run[0][0] < 6 * 3600:
        return None
    slope = _slope(run)
    per_day = -slope * DAY if slope is not None else 0
    if per_day < MIN_DRYING:
        return None
    return max(now, run[-1][0] + (soil - target) / per_day * DAY)

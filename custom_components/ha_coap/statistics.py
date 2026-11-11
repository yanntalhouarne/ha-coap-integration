"""Gaps filled in: the samples a board kept while Home Assistant couldn't reach it (or wasn't
running) become the long-term statistics of the hours that have none. Home Assistant's state
history can't be written after the fact; its statistics (the hourly mean, min and max that the
statistics graphs and the history of over 10 days show) can."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import coap
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

HOUR = 3600

# The measurement sensors: their key (unique ID "<board>_<key>"), the sample's field, unit, unit class
MEASURED = (
    ("soil-humidity", "soil_moisture", "%", None),
    ("battery", "battery", "%", None),
    ("air-humidity", "air_humidity", "%", None),
    ("temperature", "temperature", "°C", "temperature"),
)


def hourly(samples: list[tuple[float, coap.Sample]], field: str, before: float) -> dict[float, list[float]]:
    """{hour start: [values]} of the samples of the hours that ended before 'before'."""
    hours = defaultdict(list)
    for when, sample in samples:
        start = when - when % HOUR
        if start + HOUR <= before:
            hours[start].append(getattr(sample, field))
    return hours


async def async_fill_gaps(hass: HomeAssistant, device_id: str, samples: list[tuple[float, coap.Sample]],
                          now: float) -> int:
    """Imports the hours the samples cover that have no statistics yet. Returns how many hours
    (all sensors) were written. Nothing without the recorder."""
    if "recorder" not in hass.config.components or not samples:
        return 0
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
    from homeassistant.components.recorder.statistics import async_import_statistics, statistics_during_period

    registry = er.async_get(hass)
    written = 0
    for key, field, unit, unit_class in MEASURED:
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{device_id}_{key}")
        if entity_id is None:
            continue
        hours = hourly(samples, field, now)
        if not hours:
            continue
        start = datetime.fromtimestamp(min(hours), UTC)
        end = datetime.fromtimestamp(max(hours) + HOUR, UTC)
        existing = await get_instance(hass).async_add_executor_job(
            statistics_during_period, hass, start, end, {entity_id}, "hour", None, {"mean"})
        have = {row["start"] for row in existing.get(entity_id, [])}
        rows = []
        for hour, values in sorted(hours.items()):
            if hour in have or float(int(hour)) in have:
                continue
            rows.append(StatisticData(start=datetime.fromtimestamp(hour, UTC), mean=sum(values) / len(values),
                                      min=min(values), max=max(values)))
        if not rows:
            continue
        metadata = StatisticMetaData(mean_type=StatisticMeanType.ARITHMETIC, has_sum=False, name=None, source="recorder",
                                     statistic_id=entity_id, unit_class=unit_class, unit_of_measurement=unit)
        async_import_statistics(hass, metadata, rows)
        written += len(rows)
        _LOGGER.info("%s: %d hour(s) of statistics filled in from the board's samples", entity_id, len(rows))
    return written


def gap_hours(samples: list[tuple[float, coap.Sample]], now: float) -> bool:
    """Whether any sample is from an hour that ended: worth looking for gaps."""
    return any(when - when % HOUR + HOUR <= now for when, _ in samples)


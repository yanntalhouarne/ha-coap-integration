"""Gaps filled in: the samples a board kept become the statistics of the hours that have none."""

import time
from datetime import UTC, datetime

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import CONF_HOST, CONF_ID, CONF_NAME
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.ha_coap import statistics
from custom_components.ha_coap.const import DOMAIN

from .conftest import DEVICE_ID, HOST


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The recorder before Home Assistant starts (the test kit wants it first)."""
    yield


def test_hourly():
    hour = 1_000_000 * 3600
    samples = [(hour + 60, type("S", (), {"soil_moisture": 40})()), (hour + 1800, type("S", (), {"soil_moisture": 42})()),
               (hour + 3700, type("S", (), {"soil_moisture": 50})())]
    assert statistics.hourly(samples, "soil_moisture", hour + 2 * 3600) == {hour: [40, 42], hour + 3600: [50]}
    assert statistics.hourly(samples, "soil_moisture", hour + 3600 + 10) == {hour: [40, 42]}  # the hour going on: not yet


async def test_the_hours_missed_are_filled_in(recorder_mock, hass, board):
    """Home Assistant wasn't running for the last 6 hours: the board kept a sample every 15 min."""
    board.diag["up"] = 6 * 3600
    for uptime in range(0, 6 * 3600, 900):
        board.add_sample(uptime, 40 + uptime // 3600)  # 40 % the first hour, 41 the next...
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DEVICE_ID, version=2, title="basil",
                            data={CONF_NAME: "basil", CONF_HOST: HOST, CONF_ID: DEVICE_ID})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    soil = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{DEVICE_ID}_soil-humidity")
    start = datetime.fromtimestamp(time.time() - 8 * 3600, UTC)
    rows = (await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, None, {soil}, "hour", None, {"mean", "min", "max"}))[soil]
    assert len(rows) >= 5                       # the hours that ended (the one going on: Home Assistant's)
    assert {round(r["mean"]) for r in rows} <= set(range(40, 47))
    assert all(r["min"] <= r["mean"] <= r["max"] for r in rows)
    # Again: nothing written twice (the hours have statistics now)
    coordinator = entry.runtime_data
    written = await statistics.async_fill_gaps(hass, DEVICE_ID, coordinator.samples, time.time())
    await async_wait_recording_done(hass)
    assert written == 0

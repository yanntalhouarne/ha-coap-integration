"""The firmware catch-up and the plant: the board's health, its pushes, its events, watering (the
Water action, the water used, Needs water, Next watering), Repairs and Reconfigure."""

import time
from datetime import timedelta

import pytest
from homeassistant import config_entries
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, CONF_ID, CONF_NAME, STATE_OFF, STATE_ON
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ha_coap import coap
from custom_components.ha_coap.const import DOMAIN, UPDATE_INTERVAL
from custom_components.ha_coap.coordinator import UNREACHABLE_ISSUE_S

from .conftest import DEVICE_ID, HOST, FakeObserver


async def set_up(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DEVICE_ID, version=2, title="basil",
                            data={CONF_NAME: "basil", CONF_HOST: HOST, CONF_ID: DEVICE_ID})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def eid(hass, domain, key):
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{DEVICE_ID}_{key}")


def state(hass, domain, key):
    return hass.states.get(eid(hass, domain, key))


async def next_reading(hass, times=1):
    for _ in range(times):
        async_fire_time_changed(hass, dt_util.utcnow() + UPDATE_INTERVAL)
        await hass.async_block_till_done(wait_background_tasks=True)
        await hass.async_block_till_done()


async def test_its_health(hass, board):
    board.diag["power"] = "charging"
    await set_up(hass)
    assert state(hass, "sensor", "signal").state == "-71"
    assert state(hass, "sensor", "parent").state == "0xf800"
    assert state(hass, "sensor", "role").state == "child"
    assert state(hass, "sensor", "restarts").state == "7"
    assert state(hass, "sensor", "restart_cause").state == "power"
    assert state(hass, "binary_sensor", "charging").state == STATE_ON
    assert state(hass, "binary_sensor", "plugged_in").state == STATE_ON
    started = dt_util.parse_datetime(state(hass, "sensor", "last_restart").state)
    assert abs(started.timestamp() - (time.time() - 3600)) < 60
    # Older firmware: no 'diag', those are unknown
    assert eid(hass, "sensor", "soil_voltage") is not None  # disabled by default, but there


async def test_older_firmware_without_diag_or_hist(hass, board):
    board.diag = None
    board.samples = None
    await set_up(hass)
    assert state(hass, "sensor", "soil-humidity").state == "42"
    assert state(hass, "sensor", "signal").state == "unknown"
    assert state(hass, "binary_sensor", "charging").state == "unknown"
    assert not FakeObserver.instances[0].registered  # nothing to observe


async def test_restarts_and_its_button_are_events(hass, board):
    await set_up(hass)
    board.diag.update(boots=8, up=40, reset="watchdog")
    await next_reading(hass)
    restarted = state(hass, "event", "restarted")
    assert restarted.attributes["event_type"] == "watchdog" and restarted.attributes["restarts"] == 8
    board.diag["button_s"] = 6
    await next_reading(hass)
    pressed = state(hass, "event", "button_pressed")
    assert pressed.attributes["event_type"] == "pressed" and pressed.attributes["seconds"] == 6


async def test_samples_are_read_and_pushes_show_at_once(hass, board):
    board.add_sample(32, 40)
    board.add_sample(932, 39)
    await set_up(hass)
    observer = FakeObserver.instances[0]
    assert observer.registered == [(HOST, ("from=0",))]  # room for it beside the fleet manager: observed
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert [s.soil_moisture for _, s in coordinator.samples] == [40, 39]
    # Watered: the board pushes the new sample, the soil shows it before the next reading
    board.add_sample(1500, 70)
    observer.push(HOST, board.history_payload(2))
    await hass.async_block_till_done()
    assert state(hass, "sensor", "soil-humidity").state == "70"
    # The next reading asks only for what it doesn't have
    await next_reading(hass)
    assert observer.registered[-1] == (HOST, ("from=3",))


async def test_not_observed_where_there_is_room_for_one_only(hass, board):
    """Firmware keeping one observer: registering would take the fleet manager's place."""
    board.diag["observers"] = 1
    board.add_sample(32, 40)
    await set_up(hass)
    assert not FakeObserver.instances[0].registered
    assert board.asked("hist")  # read, not observed


async def test_watering(hass, board):
    await set_up(hass)
    flow = eid(hass, "number", "pump_flow")
    await hass.services.async_call("number", "set_value", {ATTR_ENTITY_ID: flow, "value": 20}, blocking=True)
    # The Water action: 5 s without changing its run time (3 s)
    await hass.services.async_call(DOMAIN, "water", {ATTR_ENTITY_ID: eid(hass, "button", "pump"), "seconds": 5},
                                   blocking=True)
    puts = [(r[1], r[3]) for r in board.requests if r[2] == coap.PUT]
    assert puts == [("pumpdc", b"5"), ("pump", b"1"), ("pumpdc", b"3")]
    assert state(hass, "binary_sensor", "pump_running").state == STATE_ON
    assert state(hass, "sensor", "last_watered").state not in ("unknown", "unavailable")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=7))
    await hass.async_block_till_done()
    assert state(hass, "binary_sensor", "pump_running").state == STATE_OFF
    # Its pump ran 5 s (the board reports it, pump_ms): 5 s × 20 ml/s = 0.1 l, seen at the next reading
    await next_reading(hass)
    assert float(state(hass, "sensor", "water_used").state) == pytest.approx(0.1)
    # Run by its button meanwhile (pump_ms counts it too)
    board.diag["pump_ms"] += 4000
    await next_reading(hass)
    assert float(state(hass, "sensor", "water_used").state) == pytest.approx(0.18)
    # The Water action on the Beep button: refused
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "water", {ATTR_ENTITY_ID: eid(hass, "button", "ping")}, blocking=True)


async def test_needs_water_and_next_watering(hass, board):
    # Drying 4 % a day for 2 days, from 40 % to 32 % now: under 30 % in about half a day
    board.diag["up"] = 2 * 86400
    for i in range(0, 2 * 86400 + 1, 3 * 3600):
        board.add_sample(i, round(40 - 4 * i / 86400))
    board.soil = 32
    await set_up(hass)
    assert state(hass, "binary_sensor", "needs_water").state == STATE_OFF
    when = dt_util.parse_datetime(state(hass, "sensor", "next_watering").state)
    assert 0.3 * 86400 < when.timestamp() - time.time() < 0.7 * 86400
    # Its target raised over the soil: it needs water now
    below = eid(hass, "number", "water_below")
    await hass.services.async_call("number", "set_value", {ATTR_ENTITY_ID: below, "value": 35}, blocking=True)
    assert state(hass, "binary_sensor", "needs_water").state == STATE_ON
    # 0: not watched
    await hass.services.async_call("number", "set_value", {ATTR_ENTITY_ID: below, "value": 0}, blocking=True)
    assert state(hass, "binary_sensor", "needs_water").state == "unknown"


async def test_a_board_unreachable_for_a_day_is_a_repair(hass, board):
    entry = await set_up(hass)
    board.online = False
    await next_reading(hass)
    coordinator = entry.runtime_data
    coordinator._down_since -= UNREACHABLE_ISSUE_S  # a day ago
    await next_reading(hass)
    issue_id = f"unreachable_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    # The fix: its new address (the same board answers there)
    board.online = True
    from custom_components.ha_coap.repairs import async_create_fix_flow
    flow = await async_create_fix_flow(hass, issue_id, {"entry_id": entry.entry_id})
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.FORM
    board.info = "b75c209,2.0,99999999"
    result = await flow.async_step_init({CONF_HOST: "fd2a::99"})
    assert result["errors"] == {"base": "another_board"}
    board.info = f"b75c209,2.0,{DEVICE_ID}"
    result = await flow.async_step_init({CONF_HOST: "[fd2a::99]"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.data[CONF_HOST] == "fd2a::99"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None  # it answers again: gone


async def test_reconfigure_a_boards_address(hass, board):
    entry = await set_up(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: "fd2a::42"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.data[CONF_HOST] == "fd2a::42" and entry.runtime_data.host == "fd2a::42"


async def test_the_menu_offers_the_fleet_manager(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert "fleet_manager" in result["menu_options"]

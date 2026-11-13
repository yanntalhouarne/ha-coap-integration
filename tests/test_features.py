"""The firmware catch-up and the plant: the board's health, its pushes, its events, watering (the
Water action, the water used, Needs water, Next watering), Repairs and Reconfigure."""

import time
from datetime import timedelta

import pytest
from homeassistant import config_entries
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, CONF_ID, CONF_NAME, STATE_OFF, STATE_ON
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ha_coap import coap
from custom_components.ha_coap.const import DOMAIN, QUIET_WHILE_OFF, UPDATE_INTERVAL
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


async def test_the_pump_run_time_follows_changes_made_elsewhere(hass, board):
    board.add_sample(32, 40)
    await set_up(hass)
    observer = FakeObserver.instances[0]
    assert float(state(hass, "number", "pump_dc").state) == 3
    # Set in the fleet manager: the board pushes without a new sample, read again at once
    board.pump_s = 7
    observer.push(HOST, board.history_payload(1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert float(state(hass, "number", "pump_dc").state) == 7
    # Or at the next reading: its 'diag' tells it, 'pumpdc' isn't asked
    asked = len(board.asked("pumpdc"))
    board.pump_s = 4
    await next_reading(hass)
    assert float(state(hass, "number", "pump_dc").state) == 4
    assert len(board.asked("pumpdc")) == asked
    # A push with a new sample is the sample, not a change of settings
    board.add_sample(932, 30)
    observer.push(HOST, board.history_payload(1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(board.asked("pumpdc")) == asked


async def test_firmware_without_it_in_diag_reads_the_pump_run_time_hourly(hass, board):
    board.diag_pump_s = False
    await set_up(hass)
    board.pump_s = 6
    await next_reading(hass)
    assert float(state(hass, "number", "pump_dc").state) == 3
    await next_reading(hass, 3)
    assert float(state(hass, "number", "pump_dc").state) == 6


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


async def unreachable_for_a_day(hass, board):
    """Set up, then not answering for a day: its Repairs issue. Returns (entry, issue ID)."""
    assert await async_setup_component(hass, "repairs", {})
    entry = await set_up(hass)
    board.online = False
    await next_reading(hass)
    entry.runtime_data._down_since -= UNREACHABLE_ISSUE_S  # a day ago
    await next_reading(hass)
    issue_id = f"unreachable_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    return entry, issue_id


async def fix(hass, issue_id, choice):
    """Opens the issue's fix, as the Repairs page does, and picks a choice in its menu."""
    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["address", "off", "remove"]
    return manager, await manager.async_configure(result["flow_id"], {"next_step_id": choice})


async def test_a_board_unreachable_for_a_day_is_a_repair(hass, board):
    entry, issue_id = await unreachable_for_a_day(hass, board)
    # The fix: its new address (the same board answers there)
    board.online = True
    manager, result = await fix(hass, issue_id, "address")
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "address"
    board.info = "b75c209,2.0,99999999"
    result = await manager.async_configure(result["flow_id"], {CONF_HOST: "fd2a::99"})
    assert result["errors"] == {"base": "another_board"}
    board.info = f"b75c209,2.0,{DEVICE_ID}"
    result = await manager.async_configure(result["flow_id"], {CONF_HOST: "[fd2a::99]"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.data[CONF_HOST] == "fd2a::99"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_a_board_off_for_now_is_no_issue_until_it_answers(hass, board):
    entry, issue_id = await unreachable_for_a_day(hass, board)
    _, result = await fix(hass, issue_id, "off")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None   # dismissed
    # Still off a day later: no issue again
    await next_reading(hass)
    if entry.runtime_data._down_since is not None:
        entry.runtime_data._down_since -= UNREACHABLE_ISSUE_S
    await next_reading(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    # Turned on: it answers, and is watched again (off for a day later: an issue)
    board.online = True
    await next_reading(hass)
    assert QUIET_WHILE_OFF not in entry.data
    board.online = False
    await next_reading(hass)
    entry.runtime_data._down_since -= UNREACHABLE_ISSUE_S
    await next_reading(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


async def test_a_board_gone_for_good_is_removed(hass, board):
    entry, issue_id = await unreachable_for_a_day(hass, board)
    manager, result = await fix(hass, issue_id, "remove")
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "remove"   # asked first
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


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

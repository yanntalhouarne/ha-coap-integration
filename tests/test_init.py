"""A board's entities: their values, unavailable when it doesn't answer, the buttons and the
pump's run time, unloading."""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, CONF_ID, CONF_NAME, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from homeassistant.util import dt as dt_util

from custom_components.ha_coap import coap
from custom_components.ha_coap.const import DOMAIN, UPDATE_INTERVAL

from .conftest import DEVICE_ID, HOST


async def set_up(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DEVICE_ID, version=2, title="basil",
                            data={CONF_NAME: "basil", CONF_HOST: HOST, CONF_ID: DEVICE_ID})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def entity_id(hass, domain, key):
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{DEVICE_ID}_{key}")


async def test_the_readings(hass, board):
    await set_up(hass)
    states = {key: hass.states.get(entity_id(hass, "sensor", key)) for key in
              ("soil-humidity", "battery", "air-humidity", "temperature", "sw-version", "hw-version", "device-id")}
    assert {k: s.state for k, s in states.items()} == {
        "soil-humidity": "42", "battery": "87", "air-humidity": "55", "temperature": "-3",  # signed
        "sw-version": "b75c209", "hw-version": "2.0", "device-id": DEVICE_ID}             # the commit whole
    assert states["soil-humidity"].attributes["friendly_name"] == "basil Soil moisture"
    assert states["soil-humidity"].attributes["state_class"] == "measurement"  # long-term statistics
    assert states["battery"].attributes["device_class"] == "battery"
    assert hass.states.get(entity_id(hass, "binary_sensor", "connectivity")).state == STATE_ON
    assert hass.states.get(entity_id(hass, "number", "pump_dc")).state == "3"
    # Its device: named after the entry, with its firmware and hardware
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DEVICE_ID)})
    assert (device.name, device.sw_version, device.hw_version) == ("basil", "b75c209", "2.0")


async def test_a_board_that_does_not_answer(hass, board):
    """Its readings show unavailable (not the last value); connectivity off. Back: back."""
    await set_up(hass)
    soil = entity_id(hass, "sensor", "soil-humidity")
    board.online = False
    async_fire_time_changed(hass, dt_util.utcnow() + UPDATE_INTERVAL)
    await hass.async_block_till_done()
    assert hass.states.get(soil).state == STATE_UNAVAILABLE
    assert hass.states.get(entity_id(hass, "binary_sensor", "connectivity")).state == STATE_OFF
    board.online, board.soil = True, 30
    async_fire_time_changed(hass, dt_util.utcnow() + UPDATE_INTERVAL * 2)
    await hass.async_block_till_done()
    assert hass.states.get(soil).state == "30"


async def test_offline_at_start_does_not_hold_up_setup(hass, board):
    """Not answering when Home Assistant starts: set up at once, its readings unavailable (never
    0); its info is asked again with its next reading."""
    board.online = False
    entry = await set_up(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(entity_id(hass, "sensor", "soil-humidity")).state == STATE_UNAVAILABLE
    board.online = True
    async_fire_time_changed(hass, dt_util.utcnow() + UPDATE_INTERVAL)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id(hass, "sensor", "sw-version")).state == "b75c209"


async def test_another_board_at_its_address(hass, board):
    """Its address now another board's: not its readings."""
    board.info = "b75c209,2.0,99999999"
    await set_up(hass)
    assert hass.states.get(entity_id(hass, "sensor", "soil-humidity")).state == STATE_UNAVAILABLE


async def test_the_buttons(hass, board):
    await set_up(hass)
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: entity_id(hass, "button", "pump")}, blocking=True)
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: entity_id(hass, "button", "ping")}, blocking=True)
    assert board.asked("pump", coap.PUT)[0][3] == b"1" and board.asked("ping", coap.PUT)[0][3] == b"1"
    board.online = False
    with pytest.raises(HomeAssistantError, match="didn't answer"):
        await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: entity_id(hass, "button", "pump")},
                                       blocking=True)


async def test_the_pump_run_time(hass, board):
    await set_up(hass)
    number = entity_id(hass, "number", "pump_dc")
    await hass.services.async_call("number", "set_value", {ATTR_ENTITY_ID: number, "value": 5}, blocking=True)
    assert board.asked("pumpdc", coap.PUT)[0][3] == b"5" and board.pump_s == 5
    assert hass.states.get(number).state == "5"


async def test_unload(hass, board):
    entry = await set_up(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED

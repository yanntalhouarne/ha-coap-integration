"""The fleet manager: connecting it, each board's firmware in Home Assistant's Updates, each
plant's water tank and battery forecast, and the plants' care tasks as a to-do list."""

import time

import pytest
from homeassistant import config_entries
from homeassistant.const import ATTR_ENTITY_ID, CONF_API_KEY, CONF_HOST, CONF_ID, CONF_NAME, CONF_URL, STATE_OFF, STATE_ON
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_coap.const import DOMAIN

from .conftest import DEVICE_ID, HOST

URL = "http://192.168.50.178:8710"
KEY = "the-key"


def firmware(image_hash="a" * 64, release_hash="b" * 64, update=None, running=False):
    return {"release": {"hash": release_hash, "version": "1.0.0", "git_commit": "3e0592d", "notes": "Pushes, history"},
            "running": running,
            "devices": [{"device_id": DEVICE_ID, "name": "basil", "image_hash": image_hash, "image_version": "1.0.0",
                         "git_commit": "2d6126b", "update": update}]}


async def test_connecting_the_fleet_manager(hass, aioclient_mock):
    aioclient_mock.get(f"{URL}/api/ha/firmware", status=403, json={"detail": "nope"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "fleet_manager"})
    assert result["step_id"] == "fleet_manager"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: "192.168.50.178:8710/",
                                                                                CONF_API_KEY: KEY})
    assert result["errors"] == {"base": "invalid_auth"}
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/api/ha/firmware", json=firmware())
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: "192.168.50.178:8710/",
                                                                                CONF_API_KEY: KEY})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_URL: URL, CONF_API_KEY: KEY}  # http:// added, the slash dropped
    assert aioclient_mock.mock_calls[-1][3]["X-HACoap-Key"] == KEY
    await hass.async_block_till_done(wait_background_tasks=True)
    # Once only
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "fleet_manager"})
    assert result["type"] is FlowResultType.ABORT


async def test_each_board_has_a_firmware_update(hass, aioclient_mock, board):
    board_entry = MockConfigEntry(domain=DOMAIN, unique_id=DEVICE_ID, version=2, title="basil",
                                  data={CONF_NAME: "basil", CONF_HOST: HOST, CONF_ID: DEVICE_ID})
    board_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(board_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    aioclient_mock.get(f"{URL}/api/ha/firmware", json=firmware())
    aioclient_mock.get(f"{URL}/api/ha/plants", status=403, json={"detail": "an older fleet manager"})
    aioclient_mock.post(f"{URL}/api/ha/update", json={"id": "job1"})
    fleet = MockConfigEntry(domain=DOMAIN, unique_id="fleet_manager", version=2, title="Fleet manager",
                            data={CONF_URL: URL, CONF_API_KEY: KEY})
    fleet.add_to_hass(hass)
    assert await hass.config_entries.async_setup(fleet.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    update = er.async_get(hass).async_get_entity_id("update", DOMAIN, f"{DEVICE_ID}_firmware_update")
    entity = er.async_get(hass).async_get(update)
    assert entity.device_id == er.async_get(hass).async_get(
        er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{DEVICE_ID}_battery")).device_id  # the board's device
    state = hass.states.get(update)
    assert state.state == STATE_ON  # an update is there
    assert (state.attributes["installed_version"], state.attributes["latest_version"]) == ("2d6126b", "3e0592d")
    assert state.attributes["release_summary"] == "Pushes, history"
    # Install: the fleet manager is asked to update that board
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/api/ha/firmware", json=firmware(update={"status": "running", "progress": 0.4,
                                                                       "step": "upload", "message": ""}, running=True))
    aioclient_mock.get(f"{URL}/api/ha/plants", status=403, json={"detail": "an older fleet manager"})
    aioclient_mock.post(f"{URL}/api/ha/update", json={"id": "job1"})
    await hass.services.async_call("update", "install", {ATTR_ENTITY_ID: update}, blocking=True)
    posts = [c for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert posts and posts[0][2] == {"device_id": DEVICE_ID}
    await hass.async_block_till_done()
    state = hass.states.get(update)
    assert state.attributes["in_progress"] is True and state.attributes["update_percentage"] == 40
    # Done: up to date
    fleet.runtime_data.async_set_updated_data(firmware(image_hash="b" * 64, update={"status": "updated", "progress": 1}))
    await hass.async_block_till_done()
    assert hass.states.get(update).state == STATE_OFF


def plants(tank=True, battery_days_left=37.6, care=None, photo=None, round_trip=None):
    now = time.time()
    return {"devices": [{"photo": photo, "round_trip": round_trip,
        "device_id": DEVICE_ID, "name": "basil", "on_usb": False, "battery_days_left": battery_days_left,
        "tank": {"size_ml": 1000, "left_ml": 150, "percent": 15, "low": True, "per_day_ml": 50, "filled": now - 17 * 86400,
                 "days_left": 3.0} if tank else None,
        "care": care if care is not None else [
            {"id": 1, "what": "Fertilize", "every_days": 14, "due": now - 3600, "last_done": None},
            {"id": 2, "what": "Repot", "every_days": 365, "due": now + 100 * 86400, "last_done": now - 265 * 86400}]}]}


async def set_up_both(hass, aioclient_mock, plant_data):
    board_entry = MockConfigEntry(domain=DOMAIN, unique_id=DEVICE_ID, version=2, title="basil",
                                  data={CONF_NAME: "basil", CONF_HOST: HOST, CONF_ID: DEVICE_ID})
    board_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(board_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    aioclient_mock.get(f"{URL}/api/ha/firmware", json=firmware())
    aioclient_mock.get(f"{URL}/api/ha/plants", json=plant_data)
    fleet = MockConfigEntry(domain=DOMAIN, unique_id="fleet_manager", version=2, title="Fleet manager",
                            data={CONF_URL: URL, CONF_API_KEY: KEY})
    fleet.add_to_hass(hass)
    assert await hass.config_entries.async_setup(fleet.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return fleet


def plant_entity(hass, domain, key):
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{DEVICE_ID}_{key}")


async def test_each_plant_has_its_water_tank_and_battery_forecast(hass, aioclient_mock, board):
    fleet = await set_up_both(hass, aioclient_mock, plants())
    tank = hass.states.get(plant_entity(hass, "sensor", "water_tank"))
    assert tank.state == "15" and tank.attributes["left_ml"] == 150 and tank.attributes["size_ml"] == 1000
    entities = er.async_get(hass)
    battery = entities.async_get_entity_id("sensor", DOMAIN, f"{DEVICE_ID}_battery")
    assert entities.async_get(tank.entity_id).device_id == entities.async_get(battery).device_id  # the board's device
    assert float(hass.states.get(plant_entity(hass, "sensor", "water_tank_days_left")).state) == 3
    assert hass.states.get(plant_entity(hass, "binary_sensor", "water_tank_low")).state == STATE_ON
    days = hass.states.get(plant_entity(hass, "sensor", "battery_days_left"))
    assert float(days.state) == pytest.approx(37.6) and days.attributes["on_usb"] is False
    # Refilled: the fleet manager is told, and read again
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/api/ha/firmware", json=firmware())
    refilled = plants()
    refilled["devices"][0]["tank"].update(left_ml=1000, percent=100, low=False, days_left=20.0)
    aioclient_mock.get(f"{URL}/api/ha/plants", json=refilled)
    aioclient_mock.post(f"{URL}/api/devices/{DEVICE_ID}/tank/refilled", json={})
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: plant_entity(hass, "button", "water_tank_refilled")},
                                   blocking=True)
    await hass.async_block_till_done()
    assert [c[1].path for c in aioclient_mock.mock_calls if c[0] == "POST"] == [f"/api/devices/{DEVICE_ID}/tank/refilled"]
    assert hass.states.get(plant_entity(hass, "sensor", "water_tank")).state == "100"
    assert hass.states.get(plant_entity(hass, "binary_sensor", "water_tank_low")).state == STATE_OFF
    # Its tank no longer tracked: unavailable; on USB: its battery's days left unknown
    fleet.runtime_data.async_set_updated_data({**firmware(), "plants": {DEVICE_ID: {
        **plants(tank=False, battery_days_left=None)["devices"][0], "on_usb": True}}})
    await hass.async_block_till_done()
    assert hass.states.get(plant_entity(hass, "sensor", "water_tank")).state == "unavailable"
    days = hass.states.get(plant_entity(hass, "sensor", "battery_days_left"))
    assert days.state == "unknown" and days.attributes["on_usb"] is True


async def test_a_plant_without_a_tank_has_no_tank_entities(hass, aioclient_mock, board):
    fleet = await set_up_both(hass, aioclient_mock, plants(tank=False))
    assert plant_entity(hass, "sensor", "water_tank") is None
    assert plant_entity(hass, "button", "water_tank_refilled") is None
    assert plant_entity(hass, "sensor", "battery_days_left") is not None
    assert plant_entity(hass, "sensor", "care_due") is not None
    # Its tank set up later: they appear
    fleet.runtime_data.async_set_updated_data({**firmware(), "plants": {DEVICE_ID: plants()["devices"][0]}})
    await hass.async_block_till_done()
    assert hass.states.get(plant_entity(hass, "sensor", "water_tank")).state == "15"


async def test_the_plants_care_tasks_are_a_to_do_list(hass, aioclient_mock, board):
    await set_up_both(hass, aioclient_mock, plants())
    todo = er.async_get(hass).async_get_entity_id("todo", DOMAIN, "fleet_manager_plant_care")
    assert hass.states.get(todo).state == "1"  # one to do now: Fertilize (Repot is done until it's due)
    care = hass.states.get(plant_entity(hass, "sensor", "care_due"))  # the plant's own, for its card
    assert care.state == "1"
    assert [(t["uid"], t["what"], t["due_now"]) for t in care.attributes["tasks"]] == [("1", "Fertilize", True),
                                                                                        ("2", "Repot", False)]
    items = await hass.services.async_call("todo", "get_items", {ATTR_ENTITY_ID: todo}, blocking=True,
                                           return_response=True)
    listed = [(i["summary"], i["status"], i["description"]) for i in items[todo]["items"]]
    assert listed == [("basil: Fertilize", "needs_action", "Every 14 days"), ("basil: Repot", "completed", "Every 365 days")]
    # Ticked: done in the fleet manager
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/api/ha/firmware", json=firmware())
    done = plants()
    done["devices"][0]["care"][0]["due"] = time.time() + 14 * 86400
    aioclient_mock.get(f"{URL}/api/ha/plants", json=done)
    aioclient_mock.post(f"{URL}/api/care/1/done", json={})
    await hass.services.async_call("todo", "update_item", {ATTR_ENTITY_ID: todo, "item": "basil: Fertilize",
                                                          "status": "completed"}, blocking=True)
    await hass.async_block_till_done()
    assert [c[1].path for c in aioclient_mock.mock_calls if c[0] == "POST"] == ["/api/care/1/done"]
    assert hass.states.get(todo).state == "0"
    # Unticked before it's due, or renamed: changed in the fleet manager, not here
    with pytest.raises(ServiceValidationError, match="next due"):
        await hass.services.async_call("todo", "update_item", {ATTR_ENTITY_ID: todo, "item": "basil: Repot",
                                                              "status": "needs_action"}, blocking=True)
    with pytest.raises(ServiceValidationError, match="fleet manager"):
        await hass.services.async_call("todo", "update_item", {ATTR_ENTITY_ID: todo, "item": "basil: Repot",
                                                              "rename": "basil: Repot it"}, blocking=True)


async def test_each_plant_has_its_photo(hass, aioclient_mock, board, hass_client):
    """The plant's newest photo in the fleet manager, as an image entity on its device (for its card)."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "http", {})
    png = b"\x89PNG\r\n\x1a\n" + b"\x01" * 32
    aioclient_mock.get(f"{URL}/api/devices/{DEVICE_ID}/photo", content=png, headers={"Content-Type": "image/png"})
    fleet = await set_up_both(hass, aioclient_mock, plants(photo=time.time() - 60))
    photo = hass.states.get(plant_entity(hass, "image", "plant_photo"))
    assert photo is not None and photo.attributes["entity_picture"].startswith("/api/image_proxy/")
    client = await hass_client()
    response = await client.get(photo.attributes["entity_picture"])
    assert response.status == 200 and await response.read() == png
    asked = len([c for c in aioclient_mock.mock_calls if c[1].path.endswith("/photo")])
    await client.get(photo.attributes["entity_picture"])  # the same photo: not read again
    assert len([c for c in aioclient_mock.mock_calls if c[1].path.endswith("/photo")]) == asked
    # A plant without a photo has none
    fleet.runtime_data.async_set_updated_data({**firmware(), "plants": {DEVICE_ID: plants(photo=None)["devices"][0]}})
    await hass.async_block_till_done()
    assert hass.states.get(photo.entity_id).state == "unavailable"


async def test_a_boards_round_trip(hass, aioclient_mock, board):
    """Its last silent ping from the fleet manager: in seconds, unknown when it didn't answer; no
    sensor before a first ping."""
    await set_up_both(hass, aioclient_mock, plants())
    assert plant_entity(hass, "sensor", "round_trip") is None
    fleet = next(e for e in hass.config_entries.async_entries(DOMAIN) if e.unique_id == "fleet_manager")
    pinged = time.time() - 60
    fleet.runtime_data.async_set_updated_data(fleet.runtime_data.data | {"plants": {p["device_id"]: p for p in plants(round_trip={"time": pinged, "ms": 1450})["devices"]}})
    await hass.async_block_till_done()
    state = hass.states.get(plant_entity(hass, "sensor", "round_trip"))
    assert state is not None, "no sensor"
    assert float(state.state) == pytest.approx(1.45) and state.attributes["unit_of_measurement"] == "s"
    assert state.attributes["answered"] is True
    entry = er.async_get(hass).async_get(state.entity_id)
    assert entry.entity_category == "diagnostic"
    fleet.runtime_data.async_set_updated_data(fleet.runtime_data.data | {"plants": {p["device_id"]: p for p in plants(round_trip={"time": pinged + 3600, "ms": None})["devices"]}})
    await hass.async_block_till_done()
    state = hass.states.get(plant_entity(hass, "sensor", "round_trip"))
    assert state.state == "unknown" and state.attributes["answered"] is False

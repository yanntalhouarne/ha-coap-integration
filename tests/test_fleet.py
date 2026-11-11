"""The fleet manager: connecting it, and each board's firmware in Home Assistant's Updates."""

from homeassistant import config_entries
from homeassistant.const import ATTR_ENTITY_ID, CONF_API_KEY, CONF_HOST, CONF_ID, CONF_NAME, CONF_URL, STATE_OFF, STATE_ON
from homeassistant.data_entry_flow import FlowResultType
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

"""Adding a board: found by zeroconf, or by its address."""

from ipaddress import ip_address

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_ID, CONF_NAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_coap.const import DOMAIN

from .conftest import DEVICE_ID, HOST

try:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:
    from homeassistant.components.zeroconf import ZeroconfServiceInfo


def discovered(name=f"ha-coap-{DEVICE_ID}", properties=None, addresses=("fe80::1", HOST)):
    return ZeroconfServiceInfo(ip_address=ip_address(addresses[0]), ip_addresses=[ip_address(a) for a in addresses],
                               port=5683, hostname=f"{name}.local.", type="_ot._udp.local.",
                               name=f"{name}._ot._udp.local.", properties=properties or {})


async def test_a_board_found_by_zeroconf(hass, board):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF},
                                                       data=discovered())
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "zeroconf_confirm"
    assert result["description_placeholders"] == {"name": f"ha-coap-{DEVICE_ID}", "host": HOST, "id": DEVICE_ID}
    flows = hass.config_entries.flow.async_progress()
    assert flows[0]["context"]["title_placeholders"] == {"name": f"ha-coap-{DEVICE_ID}"}  # its name in the list
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_NAME: "basil"})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "basil"
    assert result["data"] == {CONF_NAME: "basil", CONF_HOST: HOST, CONF_ID: DEVICE_ID}  # its routable address
    assert result["result"].unique_id == DEVICE_ID and result["result"].version == 2
    await hass.async_block_till_done()


async def test_its_id_from_its_txt_record(hass, board):
    """Newer firmware says its ID in its TXT record: whatever its name."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF},
        data=discovered(name="kitchen", properties={"id": "AABBCCDD", "fw": "3e0592d"}))
    assert result["step_id"] == "zeroconf_confirm" and result["description_placeholders"]["id"] == "aabbccdd"


async def test_a_name_without_an_id_is_ignored(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF},
                                                       data=discovered(name="some-thread-thing"))
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_device_id"


async def test_a_board_found_again_at_a_new_address(hass, board):
    """Already added: its address is updated, and its entry reloaded (it talks to the new one)."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DEVICE_ID, version=2, title="basil",
                            data={CONF_NAME: "basil", CONF_HOST: "fd11::1", CONF_ID: DEVICE_ID})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF},
                                                       data=discovered())
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST
    assert entry.runtime_data.host == HOST  # reloaded: the new address is used
    assert board.requests[-1][0] == HOST


async def test_a_board_added_by_its_address(hass, board):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["step_id"] == "user"
    board.online = False
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: f"[{HOST}]", CONF_NAME: "mint"})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "cannot_connect"}
    board.online = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: f"[{HOST}]", CONF_NAME: "mint"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_NAME: "mint", CONF_HOST: HOST, CONF_ID: DEVICE_ID}  # brackets dropped, its ID asked
    await hass.async_block_till_done()


async def test_a_board_by_its_address_without_an_id(hass, board):
    board.info = "3e0592d,1.0"  # older firmware
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: HOST, CONF_NAME: "mint"})
    assert result["errors"] == {"base": "no_device_id"}

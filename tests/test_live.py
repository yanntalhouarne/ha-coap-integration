"""Against a real board, when HA_COAP_LIVE_HOST gives its address (skipped otherwise): set up
like a discovered one, its readings and info read for real. Only reads: no pump, no beep.

    HA_COAP_LIVE_HOST=fd2a:83c:28ba:1:... HA_COAP_LIVE_ID=488bc4d9 pytest tests/test_live.py
"""

import os

import pytest
from homeassistant.const import CONF_HOST, CONF_ID, CONF_NAME, STATE_ON
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_coap.const import DOMAIN

HOST = os.environ.get("HA_COAP_LIVE_HOST")
DEVICE_ID = os.environ.get("HA_COAP_LIVE_ID", "")

pytestmark = pytest.mark.skipif(not HOST, reason="no real board (HA_COAP_LIVE_HOST)")


@pytest.fixture
def network():
    """The real network: the test kit only lets tests reach 127.0.0.1."""
    import pytest_socket
    pytest_socket._remove_restrictions()
    yield


async def test_a_real_board(hass, network):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DEVICE_ID, version=2, title="live",
                            data={CONF_NAME: "live", CONF_HOST: HOST, CONF_ID: DEVICE_ID})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    registry = er.async_get(hass)
    states = {e.unique_id.split("_", 1)[1]: hass.states.get(e.entity_id).state
              for e in er.async_entries_for_config_entry(registry, entry.entry_id) if not e.disabled_by}
    print("\n" + "\n".join(f"  {key}: {state}" for key, state in sorted(states.items())))
    assert states["connectivity"] == STATE_ON
    assert states["device-id"] == DEVICE_ID
    assert 0 <= int(states["soil-humidity"]) <= 100 and 0 <= int(states["battery"]) <= 100
    coordinator = entry.runtime_data
    diag = coordinator.data.diag
    print(f"  diag: {diag}\n  samples kept here: {len(coordinator.samples)}")
    if diag is not None:  # newer firmware
        assert states["restarts"] == str(diag.boots) and states["role"] in ("child", "router", "leader")
        # Firmware with one observer: not registered (the fleet manager keeps its place)
        assert (diag.observers >= 2) == bool(coordinator.observer and coordinator.observer._tokens)
    await hass.config_entries.async_unload(entry.entry_id)

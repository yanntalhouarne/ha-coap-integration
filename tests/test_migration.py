"""Version 1 → 2: the board's real ID in its entry, device and entities, keeping the entity IDs
(and so their history)."""

from homeassistant.const import CONF_HOST, CONF_ID, CONF_NAME
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_coap.const import DOMAIN

from .conftest import DEVICE_ID, HOST

# What version 1 made, for a board discovered as "ha-coap-1a2b3c4d": its ID after the first dash,
# its sensors' unique IDs without a separator, its buttons' and slider's with one
OLD_ID = f"coap-{DEVICE_ID}"
OLD_ENTITIES = [
    ("sensor", f"{OLD_ID}soil-humidity", "sensor.basil_soil_humidity"),
    ("sensor", f"{OLD_ID}battery", "sensor.basil_battery"),
    ("sensor", f"{OLD_ID}air-humidity", "sensor.basil_air_humidity"),
    ("sensor", f"{OLD_ID}temperature", "sensor.basil_temperature"),
    ("sensor", f"{OLD_ID}sw-version", "sensor.basil_sw_version"),
    ("sensor", f"{OLD_ID}hw-version", "sensor.basil_hw_version"),
    ("sensor", f"{OLD_ID}device-id", "sensor.basil_device_id"),
    ("binary_sensor", f"{OLD_ID}connectivity", "binary_sensor.basil_connectivity"),
    ("button", f"{OLD_ID}_pump", "button.basil_pump"),
    ("button", f"{OLD_ID}_ping", "button.basil_ping"),
    ("number", f"{OLD_ID}_pump_dc", "number.basil_pump_duty_cycle"),
]


def version_1(hass, unique_id=OLD_ID, data_id=OLD_ID, host=HOST, entities=OLD_ENTITIES):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=unique_id, version=1, title="basil",
                            data={CONF_NAME: "basil", CONF_HOST: host, CONF_ID: data_id})
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, data_id)},
                                                    name="basil.soil-humidity", manufacturer="Yann T.")
    registry = er.async_get(hass)
    for domain, unique_id_, entity_id in entities:
        registry.async_get_or_create(domain, DOMAIN, unique_id_, config_entry=entry, device_id=device.id,
                                     suggested_object_id=entity_id.split(".", 1)[1])
    return entry, device


async def test_a_board_discovered_by_version_1(hass, board):
    entry, device = version_1(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert (entry.version, entry.unique_id, entry.data[CONF_ID]) == (2, DEVICE_ID, DEVICE_ID)
    registry = er.async_get(hass)
    for domain, old_unique_id, entity_id in OLD_ENTITIES:
        key = old_unique_id[len(OLD_ID):].lstrip("_")
        assert registry.async_get_entity_id(domain, DOMAIN, f"{DEVICE_ID}_{key}") == entity_id  # same entity ID
        assert registry.async_get_entity_id(domain, DOMAIN, old_unique_id) is None
    # The same device, with the board's ID; named after the entry once its entities are set up
    moved = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DEVICE_ID)})
    assert moved.id == device.id and moved.name == "basil"
    # No second entity beside each old one (only the new kinds of entities are new), and they work
    unique_ids = [e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)]
    assert len(unique_ids) == len(set(unique_ids)) and not any(u.startswith(OLD_ID) for u in unique_ids)
    assert len([e for e in er.async_entries_for_config_entry(registry, entry.entry_id)
                if e.entity_id in {old[2] for old in OLD_ENTITIES}]) == len(OLD_ENTITIES)
    assert hass.states.get("sensor.basil_soil_humidity").state == "42"
    assert hass.states.get("number.basil_pump_duty_cycle").state == "3"


async def test_a_board_added_by_hand_in_version_1(hass, board):
    """Its ID typed in (right, but its entities' IDs lack the underscore), its address in brackets."""
    entities = [(d, u.replace(OLD_ID, DEVICE_ID), e) for d, u, e in OLD_ENTITIES]
    entry, _ = version_1(hass, unique_id=None, data_id=DEVICE_ID, host=f"[{HOST}]", entities=entities)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert (entry.version, entry.unique_id, entry.data[CONF_HOST]) == (2, DEVICE_ID, HOST)
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{DEVICE_ID}_soil-humidity") == "sensor.basil_soil_humidity"
    assert hass.states.get("sensor.basil_soil_humidity").state == "42"


async def test_an_id_without_a_board_id_is_kept(hass, board):
    entry, _ = version_1(hass, unique_id="kitchen", data_id="kitchen", entities=[])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert (entry.version, entry.unique_id) == (2, "kitchen")


async def test_two_entries_for_one_board(hass, board):
    """The board added twice in version 1 (once by hand with its ID): the second keeps its ID."""
    MockConfigEntry(domain=DOMAIN, unique_id=DEVICE_ID, version=2, title="mint",
                    data={CONF_NAME: "mint", CONF_HOST: HOST, CONF_ID: DEVICE_ID}).add_to_hass(hass)
    entry, _ = version_1(hass, entities=OLD_ENTITIES[:1])
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert (entry.version, entry.unique_id) == (2, OLD_ID)
    # It still works: its ID kept, the board's own ID in it is what's checked
    assert hass.states.get("sensor.basil_soil_humidity").state == "42"

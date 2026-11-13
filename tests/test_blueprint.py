"""The blueprint: Home Assistant reads it, and an automation made from it waters when the plant
needs water, in its hours, not twice within its wait."""

from pathlib import Path
from unittest.mock import patch

from homeassistant.components.blueprint import models
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import load_yaml_dict

BLUEPRINT = Path(__file__).parents[1] / "blueprints" / "automation" / "ha_coap" / "water_when_dry.yaml"


def test_it_is_a_valid_blueprint():
    blueprint = models.Blueprint(load_yaml_dict(str(BLUEPRINT)), expected_domain="automation", schema=BLUEPRINT_SCHEMA)
    assert set(blueprint.inputs) == {"needs_water", "pump", "seconds", "after", "before", "wait_hours"}


def install(config_dir: str) -> None:
    """The blueprint in Home Assistant's blueprints folder, as importing it would put it."""
    target = Path(config_dir) / "blueprints" / "automation" / "ha_coap"
    target.mkdir(parents=True, exist_ok=True)
    (target / "water_when_dry.yaml").write_text(BLUEPRINT.read_text(encoding="utf-8"), encoding="utf-8")


async def test_it_waters_when_the_plant_needs_water(hass):
    await hass.async_add_executor_job(install, hass.config.config_dir)
    watered = []

    async def water(call):
        watered.append((call.data.get("entity_id"), call.data.get("seconds")))

    hass.services.async_register("ha_coap", "water", water)
    hass.states.async_set("binary_sensor.basil_needs_water", "off")
    assert await async_setup_component(hass, "automation", {"automation": {
        "id": "water_basil", "use_blueprint": {"path": "ha_coap/water_when_dry.yaml", "input": {
            "needs_water": "binary_sensor.basil_needs_water", "pump": "button.basil_run_the_pump", "seconds": 4,
            "after": "00:00:00", "before": "23:59:59", "wait_hours": 3}}}})
    await hass.async_block_till_done()
    noon = dt_util.now().replace(hour=12, minute=0)
    with patch("homeassistant.helpers.condition.dt_util.now", return_value=noon):
        hass.states.async_set("binary_sensor.basil_needs_water", "on")
        await hass.async_block_till_done()
        assert len(watered) == 1 and watered[0][1] == 4
        # Still dry soon after: not again within its wait
        hass.states.async_set("binary_sensor.basil_needs_water", "off")
        hass.states.async_set("binary_sensor.basil_needs_water", "on")
        await hass.async_block_till_done()
        assert len(watered) == 1


TANK_LOW = BLUEPRINT.with_name("tank_low.yaml")


def test_the_tank_blueprint_is_valid():
    blueprint = models.Blueprint(load_yaml_dict(str(TANK_LOW)), expected_domain="automation", schema=BLUEPRINT_SCHEMA)
    assert set(blueprint.inputs) == {"tank_low", "in_home_assistant", "actions"}


def install_tank_low(config_dir: str) -> None:
    target = Path(config_dir) / "blueprints" / "automation" / "ha_coap"
    target.mkdir(parents=True, exist_ok=True)
    (target / "tank_low.yaml").write_text(TANK_LOW.read_text(encoding="utf-8"), encoding="utf-8")


async def test_it_says_when_a_tank_is_low(hass):
    await hass.async_add_executor_job(install_tank_low, hass.config.config_dir)
    told = []

    async def phone(call):
        told.append(call.data["message"])

    hass.services.async_register("notify", "phone", phone)
    assert await async_setup_component(hass, "persistent_notification", {})
    basil, mint = {"friendly_name": "basil Water tank low"}, {"friendly_name": "mint Water tank low"}
    hass.states.async_set("binary_sensor.basil_water_tank_low", "off", basil)
    hass.states.async_set("binary_sensor.mint_water_tank_low", "off", mint)
    assert await async_setup_component(hass, "automation", {"automation": {
        "id": "tanks", "use_blueprint": {"path": "ha_coap/tank_low.yaml", "input": {
            "tank_low": ["binary_sensor.basil_water_tank_low", "binary_sensor.mint_water_tank_low"],
            "actions": [{"action": "notify.phone", "data": {"message": "{{ message }}"}}]}}}})
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.basil_water_tank_low", "on", basil)
    await hass.async_block_till_done()
    assert told == ["basil's water tank is low: refill it, then press its Water tank refilled button."]
    # Still low: not said again; another plant's: said
    hass.states.async_set("binary_sensor.basil_water_tank_low", "on", {**basil, "x": 1})
    hass.states.async_set("binary_sensor.mint_water_tank_low", "on", mint)
    await hass.async_block_till_done()
    assert told[1:] == ["mint's water tank is low: refill it, then press its Water tank refilled button."]

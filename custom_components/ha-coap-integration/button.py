"""HA CoAp Button Interface."""
import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_ID
from homeassistant.helpers.entity import DeviceInfo
from homeassistant import config_entries, core
from aiocoap import Message, Context
from aiocoap.numbers.codes import Code
from aiocoap.numbers.types import Type
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONST_COAP_PROTOCOL = "coap://"
CONST_COAP_PUMP_URI = "pump"

async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):
    """Set up the button platform."""
    config = hass.data[DOMAIN].get(config_entry.entry_id)
    _LOGGER.info(
        "Setting up button entity for device %s with unique ID %s",
        config[CONF_NAME],
        config[CONF_ID]
    )
    
    protocol = await Context.create_client_context()
    
    button = CoAPPumpButton(
        protocol,
        "[" + config[CONF_HOST] + "]",
        config[CONF_NAME],
        config[CONF_ID],
    )
    
    async_add_entities([button])
    _LOGGER.info("-> Added pump button for device %s", config[CONF_NAME])

class CoAPPumpButton(ButtonEntity):
    """Representation of a CoAP pump button."""

    def __init__(self, protocol, host, name, device_id):
        """Initialize the button entity."""
        self._protocol = protocol
        self._host = host
        self._attr_name = f"{name}.pump"
        self._attr_unique_id = f"{device_id}_pump"
        self._device_id = device_id
        self._attr_icon = "mdi:water-pump"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self.name,
            manufacturer="Yann T.",
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            request = Message(
                mtype=Type.CON,
                code=Code.PUT,
                payload=b"1",  # Send "1" to activate pump
                uri=f"{CONST_COAP_PROTOCOL}{self._host}/{CONST_COAP_PUMP_URI}"
            )
            _LOGGER.debug(
                "Sending CON PUT request to %s/%s (%s)",
                self.name,
                CONST_COAP_PUMP_URI,
                self._host
            )
            response = await self._protocol.request(request).response
            if response:
                _LOGGER.debug(
                    "Successfully activated pump for %s",
                    self.name
                )
            
        except Exception as e:
            _LOGGER.error(
                "Failed to activate pump for %s: %s",
                self.name,
                str(e)
            )
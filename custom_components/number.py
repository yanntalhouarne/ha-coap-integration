"""HA CoAp Number Interface for Pump Duty Cycle Control."""
import logging
from homeassistant.components.number import (
    NumberEntity,
    NumberMode,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_ID
from homeassistant.helpers.entity import DeviceInfo
from homeassistant import config_entries, core
from aiocoap import Message, Context
from aiocoap.numbers.codes import Code
from aiocoap.numbers.types import Type
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONST_COAP_PROTOCOL = "coap://"
CONST_COAP_PUMPDC_URI = "pumpdc"

async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):
    """Set up the number platform."""
    config = hass.data[DOMAIN].get(config_entry.entry_id)
    _LOGGER.info(
        "Setting up pump duty cycle control for device %s with unique ID %s",
        config[CONF_NAME],
        config[CONF_ID]
    )
    
    protocol = await Context.create_client_context()
    
    number = CoAPPumpDutyCycle(
        protocol,
        "[" + config[CONF_HOST] + "]",
        config[CONF_NAME],
        config[CONF_ID],
    )
    
    async_add_entities([number])
    _LOGGER.info("-> Added pump duty cycle control for device %s", config[CONF_NAME])

class CoAPPumpDutyCycle(NumberEntity):
    """Representation of a CoAP pump duty cycle control."""

    def __init__(self, protocol, host, name, device_id):
        """Initialize the number entity."""
        self._protocol = protocol
        self._host = host
        self._attr_name = f"{name}.pump_duty_cycle"
        self._attr_unique_id = f"{device_id}_pump_dc"
        self._device_id = device_id
        self._attr_native_min_value = 1
        self._attr_native_max_value = 9
        self._attr_native_step = 1
        self._attr_mode = NumberMode.SLIDER
        self._attr_native_value = 1  # Default value set to 1
        self._attr_icon = "mdi:pump"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self.name,
            manufacturer="Yann T.",
        )

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        try:
            int_value = int(value)
            request = Message(
                mtype=Type.CON,
                code=Code.PUT,
                payload=str(int_value).encode("ascii"),
                uri=f"{CONST_COAP_PROTOCOL}{self._host}/{CONST_COAP_PUMPDC_URI}"
            )
            _LOGGER.debug(
                "Sending CON PUT request with payload '%d' to %s/%s (%s)",
                int_value,
                self.name,
                CONST_COAP_PUMPDC_URI,
                self._host
            )
            response = await self._protocol.request(request).response
            if response:
                self._attr_native_value = int_value
                _LOGGER.debug(
                    "Successfully set pump duty cycle to %d for %s",
                    int_value,
                    self.name
                )
            
        except Exception as e:
            _LOGGER.error(
                "Failed to set pump duty cycle for %s: %s",
                self.name,
                str(e)
            )
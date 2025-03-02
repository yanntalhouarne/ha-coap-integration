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
    
    # Get initial value before adding entity
    await number.async_get_initial_value()
    
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
        self._attr_native_value = 1  # Default value
        self._attr_icon = "mdi:pump"

    async def async_get_initial_value(self) -> None:
        """Get the initial pump duty cycle value from the server."""
        try:
            request = Message(
                mtype=Type.CON,
                code=Code.GET,
                uri=f"{CONST_COAP_PROTOCOL}{self._host}/{CONST_COAP_PUMPDC_URI}"
            )
            _LOGGER.debug(
                "Sending CON GET request to get initial pump duty cycle from %s/%s (%s)",
                self.name,
                CONST_COAP_PUMPDC_URI,
                self._host
            )
            response = await self._protocol.request(request).response
            if response and response.payload:
                # Handle single byte response
                value = int.from_bytes(response.payload, byteorder='big')
                if 1 <= value <= 9:  # Validate the value is in range
                    self._attr_native_value = value
                    _LOGGER.debug(
                        "Successfully got initial pump duty cycle value: %d for %s",
                        value,
                        self.name
                    )
                else:
                    _LOGGER.warning(
                        "Received pump duty cycle value %d is out of range (1-9) for %s",
                        value,
                        self.name
                    )
            
        except Exception as e:
            _LOGGER.error(
                "Failed to get initial pump duty cycle value for %s: %s",
                self.name,
                str(e)
            )

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
            int_value = int(value+48)
            # Convert integer to single byte
            payload = int_value.to_bytes(1, byteorder='big')
            request = Message(
                mtype=Type.CON,
                code=Code.PUT,
                payload=payload,
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
                self._attr_native_value = int_value-48
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
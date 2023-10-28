"""HA CoAp Switch Interface."""
import sys
#sys.path.append("/config/custom_components/ha-coap-integration")

from datetime import timedelta
import logging

# Bring in CoAP
from aiocoap import *

import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_ID,
    TEMP_CELSIUS,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant import config_entries, core


from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONST_DEFAULT_SCAN_PERIOD_S = 60.0

CONST_COAP_PROTOCOL = "coap://"
CONST_COAP_STRING_TRUE = "1"
CONST_COAP_STRING_FALSE = "0"

# protocol = ""

# for data validation
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_ID): cv.string,
    }
)

# setup platform when new device is discovered by zeroconf
async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):
    _LOGGER.info("In async_setup_entry()...")
    config = hass.data[DOMAIN].get(config_entry.entry_id)
    _LOGGER.info("Setting up entry for temperature entity of %s with unique ID %s", config[CONF_NAME], config[CONF_ID])
    protocol = await Context.create_client_context()
    hass_sensors = []
    hass_sensors.append(
        CoAPsensorNode(
            "["+config[CONF_HOST]+"]",
            "temperature",
            protocol,
            config[CONF_NAME],
            TEMP_CELSIUS,
            1,
            config[CONF_ID],
        )
    )

    async_add_entities(hass_sensors)

    async def async_update_sensors(event):
        """Update temperature sensor."""
        # Update sensors based on scan_period set below which comes in from the config
        for sensor in hass_sensors:
            await sensor.async_update_values()
    # update sensor every 5 seconds
    async_track_time_interval(hass, async_update_sensors, timedelta(seconds=CONST_DEFAULT_SCAN_PERIOD_S))

class CoAPsensorNode(Entity):
    """Representation of a CoAP sensor node."""

    def __init__(self, host, uri, protocol, name, unit, round_places, device_id):
        """Initialize the sensor."""

        #_LOGGER.info("Adding temp sensor " + name + " with address " + host)

        self._uri = uri
        self._name = "temp."+name
        self._unit = unit
        self._round_places = round_places
        self._state = "0"
        self._host = host
        self._protocol = protocol
        self._device_id = device_id

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit the value is expressed in."""
        return self._unit

    @property
    def poll(self):
        """Sensors are polled."""
        return True

    @property
    def device_id(self):
        """Return the ID of this roller."""
        return self._device_id
    
    @property
    def unique_id(self):
        """Return a unique identifier for this sensor."""
        return f"{self._device_id}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, self._device_id)
            },
            name=self.name,
            manufacturer="Yann T.",
            model="version 0.1",
        )
    
    @property
    def icon(self):
        """Return the icon of the device."""
        return "mdi:thermometer"

    @callback
    async def async_update_values(self):
        """Update this sensor."""
        try:
            request = Message(mtype=NON, code=GET)
            _uri = CONST_COAP_PROTOCOL + self._host + "/" + self._uri
            request.set_request_uri(uri=_uri)
            response = await self._protocol.request(request).response
            _LOGGER.info("Received " + str(int.from_bytes(response.payload)) + " from " + self._host + "/" + self._uri)
	        # Check for change
            if self._state != round(float(int.from_bytes(response.payload)), self._round_places):
                # Round result to make the ui look nice
                self._state = round(float(int.from_bytes(response.payload)), self._round_places)
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.info("Exception - Failed to GET temperature resource from " + self._name + "/" + self._uri)
            _LOGGER.info(e)

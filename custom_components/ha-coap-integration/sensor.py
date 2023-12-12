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
    PERCENTAGE,
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
    config = hass.data[DOMAIN].get(config_entry.entry_id)
    _LOGGER.info("Setting up entry for temperature entity of %s with unique ID %s", config[CONF_NAME], config[CONF_ID])
    protocol = await Context.create_client_context()
    sensors = []
    sensors.append(
        CoAPsensorNode(
            "["+config[CONF_HOST]+"]",
            "soil_humidity",
            protocol,
            config[CONF_NAME],
            PERCENTAGE,
            1,
            config[CONF_ID],
        )
    )
    sensors.append(
        CoAPsensorNode(
            "["+config[CONF_HOST]+"]",
            "battery",
            protocol,
            config[CONF_NAME],
            PERCENTAGE,
            1,
            config[CONF_ID],
        )
    )
    sensors.append(
        CoAPsensorNode(
            "["+config[CONF_HOST]+"]",
            "air_humidity",
            protocol,
            config[CONF_NAME],
            PERCENTAGE,
            1,
            config[CONF_ID],
        )
    )
    sensors.append(
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
    # add sensors to sensor manager
    sensor_manager = HACoApSensorManager(protocol, "temperature", "["+config[CONF_HOST]+"]", config[CONF_NAME], sensors)
    _LOGGER.info("Size of sensors is: %s", len(sensors))
    async_add_entities(sensors)
    #hass.async_add_job(endpoint.async_get_data)
    #async_track_time_interval(hass, endpoint.async_get_data, SCAN_INTERVAL)

    # get sensor data 
    sensor_manager.async_get_data()

    # also get data periodically
    async def async_update_sensors(event):
        """Update temperature sensor."""
        # Update sensors based on scan_period set below which comes in from the config
        #for sensor in sensors:
        #    await sensor.async_update_values()
        await sensor_manager.async_get_data()
    # update sensor every 60 seconds
    async_track_time_interval(hass, sensor_manager.async_get_data, timedelta(seconds=CONST_DEFAULT_SCAN_PERIOD_S))

class HACoApSensorManager:
    """Manages Sensors of a HA-CoAp Device"""

    def __init__(self, protocol, uri, host, name, sensors):
        """Initialize the sensor manager."""
        #self._hass = hass
        self._protocol = protocol
        self._uri = uri
        self._host = host
        self._name = name
        self._sensors = sensors

    async def async_get_data(self, now=None):
        """Update this sensor."""
        try:
            _LOGGER.info("In  async_get_data()...")
            request = Message(mtype=NON, code=GET)
            _uri = CONST_COAP_PROTOCOL + self._host + "/" + self._uri
            request.set_request_uri(uri=_uri)
            response = await self._protocol.request(request).response
            #_LOGGER.info("Received " + response.payload + " from " + self._name + "/" + self._uri)
            self._sensors[0]._state = round(float(response.payload[0]), self._sensors[0]._round_places)
            self._sensors[1]._state = round(float(response.payload[1]), self._sensors[1]._round_places)
            self._sensors[2]._state = round(float(response.payload[2]), self._sensors[2]._round_places)
            self._sensors[3]._state = round(float(response.payload[3]), self._sensors[3]._round_places)
            #_LOGGER.info("Temperature = " + self._sensors[0]._state + ", Battery = " + self._sensors[1]._state)
            for sensor in self._sensors:
                sensor.async_write_ha_state()
        except Exception as e:
            _LOGGER.info("Exception - Failed to GET temperature resource from " + self._name + "/" + self._uri)
            _LOGGER.info(e)


class CoAPsensorNode(Entity):
    """Representation of a CoAP sensor node."""

    def __init__(self, host, uri, protocol, name, unit, round_places, device_id):
        """Initialize the sensor."""

        #_LOGGER.info("Adding temp sensor " + name + " with address " + host)

        self._uri = uri
        self._name = name + "." + uri
        self._unit = unit
        self._round_places = round_places
        self._state = "0"
        self._host = host
        self._protocol = protocol
        self._device_id = device_id
        self._unique_id = device_id + uri

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
        return f"{self._unique_id}"

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
            model="hw: v1.0 | fw: 9a19597",
        )

    @property
    def icon(self):
        """Return the icon of the device."""
        if self._uri == "soil_humidity":
            return "mdi:flower"
        elif self._uri == "air_humidity":
            return "mdi:water"
        elif self._uri == "temperature":
            return "mdi:thermometer"
        elif self._uri == "battery":
            return "mdi:battery"
        else:
            return "mdi:cat"

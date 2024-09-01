"""HA CoAp Switch Interface."""
import sys
#sys.path.append("/config/custom_components/ha-coap-integration")

from datetime import timedelta
import logging

import asyncio

import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_ID,
    UnitOfTemperature,
    PERCENTAGE,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant import config_entries, core

# aiocoap
import aiocoap.defaults
# Set new values for ACK timeout and max retransmissions
aiocoap.defaults.ACK_TIMEOUT = 10.0  # Wait 5 seconds for an ACK
aiocoap.defaults.MAX_RETRANSMIT = 3  # Retransmit up to 5 times
# Bring in CoAP
from aiocoap import *

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONST_DEFAULT_SCAN_PERIOD_S = 900 # 15min
CONST_INFO_SCAN_PERIOD_S = 3600 # 1hr

CONST_COAP_PROTOCOL = "coap://"
CONST_COAP_STRING_TRUE = "1"
CONST_COAP_STRING_FALSE = "0"

CONST_COAP_DATA_URI = "temperature"
CONST_COAP_INFO_URI = "info"

# protocol = ""

# for data validation
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_ID): cv.string,
        #vol.Required(CONF_MODEL): cv.string,
    }
)

# setup platform when new device is discovered by zeroconf
async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):
    config = hass.data[DOMAIN].get(config_entry.entry_id)
    _LOGGER.info("Setting up entry for data entities of device %s with unique ID %s", config[CONF_NAME], config[CONF_ID])
    protocol = await Context.create_client_context()
    sensors = []
    sensors.append(
        CoAPsensorNode(
            "soil_humidity",
            CONST_COAP_DATA_URI,
            config[CONF_NAME],
            PERCENTAGE,
            1,
            config[CONF_ID],
        )
    )
    sensors.append(
        CoAPsensorNode(
            "battery",
            CONST_COAP_DATA_URI,
            config[CONF_NAME],
            PERCENTAGE,
            1,
            config[CONF_ID],
        )
    )
    sensors.append(
        CoAPsensorNode(
            "air_humidity",
            CONST_COAP_DATA_URI,
            config[CONF_NAME],
            PERCENTAGE,
            1,
            config[CONF_ID],
        )
    )
    sensors.append(
        CoAPsensorNode(
            "temperature",
            CONST_COAP_DATA_URI,
            config[CONF_NAME],
            UnitOfTemperature.CELSIUS,
            1,
            config[CONF_ID],
        )
    )
    sensors.append(
        CoAPsensorNode(
            "info",
            CONST_COAP_INFO_URI,
            config[CONF_NAME],
            None,
            1,
            config[CONF_ID],
        )
    )
    sensors.append(
        CoAPsensorNode(
            "info-hw",
            CONST_COAP_INFO_URI,
            config[CONF_NAME],
            None,
            1,
            config[CONF_ID],
        )
    )
    # add sensors to sensor manager
    sensor_manager = HACoApSensorManager(protocol, "["+config[CONF_HOST]+"]", config[CONF_NAME], sensors)
    _LOGGER.info("-> %s data entities have been added to device %s", len(sensors), config[CONF_NAME])
    async_add_entities(sensors)
    for sensor in sensors:
        _LOGGER.info("- %s", sensor._sensor_type)
    #hass.async_add_job(endpoint.async_get_data)
    #async_track_time_interval(hass, endpoint.async_get_data, SCAN_INTERVAL)

    # get device data 
    await sensor_manager.async_get_data()
    #await asyncio.sleep(3)
    # get device info
    await sensor_manager.async_get_info()
    
    # also get data periodically
    # async def async_update_sensors(event):
    #     """Update temperature sensor."""
    #     # Update sensors based on scan_period set below which comes in from the config
    #     #for sensor in sensors:
    #     #    await sensor.async_update_values()
    #     await sensor_manager.async_get_data()
    # update sensor data every CONST_DEFAULT_SCAN_PERIOD_S seconds
    async_track_time_interval(hass, sensor_manager.async_get_data, timedelta(seconds=CONST_DEFAULT_SCAN_PERIOD_S))
    _LOGGER.info(" -> Data will be updated every %s seconds", CONST_DEFAULT_SCAN_PERIOD_S)
    # update sensor every CONST_INFO_SCAN_PERIOD_S seconds
    async_track_time_interval(hass, sensor_manager.async_get_info, timedelta(seconds=CONST_INFO_SCAN_PERIOD_S))
    _LOGGER.info(" -> Info will be updated every %s seconds", CONST_INFO_SCAN_PERIOD_S)

class HACoApSensorManager:
    """Manages Sensors of a HA-CoAp Device"""

    def __init__(self, protocol, host, name, sensors):
        """Initialize the sensor manager."""
        #self._hass = hass
        self._protocol = protocol
        self._host = host
        self._name = name
        self._info = " "
        self._sensors = sensors

    async def async_get_data(self, now=None):
        """Update the device data."""
        try:
            # make sure all data sensors have the same URI 
            for sensor in self._sensors[:4]:
                if sensor.uri != CONST_COAP_DATA_URI:
                    raise Exception("In device %s, not all data sensors have the same URI.")
            request = Message(mtype=NON, code=GET)
            _uri = CONST_COAP_PROTOCOL+self._host+"/"+CONST_COAP_DATA_URI
            _LOGGER.info("Sending NON GET request to " +  self._name+"/"+CONST_COAP_DATA_URI+"(" + self._host +")")
            request.set_request_uri(uri=_uri)
            response = await self._protocol.request(request).response
        except Exception as e:
            _LOGGER.info("-> Exception - Failed to GET '"+CONST_COAP_DATA_URI+"'resource  (mid = "+str(request.mid)+") from "+self._name+"/"+CONST_COAP_DATA_URI)
            _LOGGER.info(e)
        else:
            _LOGGER.info("-> Received data (mid = "+str(request.mid)+") from "+self._name+"/"+CONST_COAP_DATA_URI+"("+ self._host +")")
            self._sensors[0]._state = round(float(response.payload[0]), self._sensors[0]._round_places)
            self._sensors[1]._state = round(float(response.payload[1]), self._sensors[1]._round_places)
            self._sensors[2]._state = round(float(response.payload[2]), self._sensors[2]._round_places)
            self._sensors[3]._state = round(float(response.payload[3]), self._sensors[3]._round_places)
            _LOGGER.info("- Soil humidity = "+str(self._sensors[0]._state)+", Battery = "+str(self._sensors[1]._state)+", Air humidity = "+str(self._sensors[2]._state)+", Temperature = "+str(self._sensors[3]._state))
            for sensor in self._sensors[:4]:
                sensor.async_write_ha_state()
            #_LOGGER.info("Device data updated...")

    async def async_get_info(self, now=None):
        """Update the device info."""
        try:
            _LOGGER.info("In  async_get_info()...")
            request = Message(mtype=CON, code=GET)
            _uri = CONST_COAP_PROTOCOL+self._host+"/"+CONST_COAP_INFO_URI
            _LOGGER.info("Sending CON GET request to " +  self._name+"/"+CONST_COAP_INFO_URI+"(" + self._host +")")
            request.set_request_uri(uri=_uri)
            response = await self._protocol.request(request).response
        except Exception as e:
            _LOGGER.info("-> Exception - Failed to GET '"+CONST_COAP_INFO_URI+"' resource (mid = "+str(request.mid)+") from "+self._name+"/"+CONST_COAP_INFO_URI)
            _LOGGER.info(e)
        else:
            #_LOGGER.info("New data received...")
            _LOGGER.info("-> Received data (mid = "+str(request.mid)+") from "+self._name+"/"+CONST_COAP_INFO_URI+" ("+self._host +")")
            self._sensors[4]._state, self._sensors[5]._state = str(response.payload).rsplit(',', 1)
            _LOGGER.info("- FW version is: " + self._sensors[4]._state.strip('b\''))
            _LOGGER.info("- HW version is: " + self._sensors[5]._state[: -5])
            self._sensors[4]._state = self._sensors[4]._state.strip('b\'')
            self._sensors[5]._state = self._sensors[5]._state[: -5]
            # update each data sensor's info entity
            for sensor in self._sensors[:4]:
                sensor._info = str(response.payload)
            # update sensor manager's info
            self._info = str(response.payload)
            #_LOGGER.info("Device info fetched...")


class CoAPsensorNode(Entity):
    """Representation of a CoAP sensor node."""

    def __init__(self, sensor_type, uri, name, unit, round_places, device_id):
        """Initialize the sensor."""

        self._sensor_type = sensor_type
        self._uri = uri
        self._name = name + "." + sensor_type
        self._unit = unit
        self._round_places = round_places
        self._state = 0
        self._device_id = device_id
        self._unique_id = device_id + sensor_type
        self._info = " "

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def info(self):
        """Return device info."""
        return self._info

    @property
    def unit_of_measurement(self):
        """Return the unit the value is expressed in."""
        return self._unit

    @property
    def poll(self):
        """Sensor is polled."""
        return True

    @property
    def device_id(self):
        """Return the ID of the sensor."""
        return self._device_id

    @property
    def unique_id(self):
        """Return a unique identifier for the sensor."""
        return f"{self._unique_id}"

    @property
    def uri(self):
        """Return the COAP uri of the sensor."""
        return f"{self._uri}"    

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
            model=self._info
        )

    @property
    def icon(self):
        """Return the icon of the device."""
        if self._sensor_type == "soil_humidity":
            return "mdi:flower"
        elif self._sensor_type == "air_humidity":
            return "mdi:water"
        elif self._sensor_type == "temperature":
            return "mdi:thermometer"
        elif self._sensor_type == "battery":
            return "mdi:battery"
        elif self._sensor_type == "info":
            return "mdi:github"
        elif self._sensor_type == "info-hw":
            return "mdi:information" 
        else:
            return "mdi:cat"

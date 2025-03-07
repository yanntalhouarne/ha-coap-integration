"""HA CoAp Sensor Interface."""
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

CONST_DATA_SCAN_PERIOD_S = 900 # 15min
CONST_INFO_SCAN_PERIOD_S = 3600 # 1hr

CONST_COAP_PROTOCOL = "coap://"
CONST_COAP_STRING_TRUE = "1"
CONST_COAP_STRING_FALSE = "0"

CONST_COAP_DATA_URI = "data"
CONST_COAP_INFO_URI = "info"

CONST_COAP_NON_TIMEOUT_S = 10 # In seconds. This is the timeout value for a non-confirmable request.

if CONST_DATA_SCAN_PERIOD_S <= CONST_COAP_NON_TIMEOUT_S:
    raise Exception("Scan period of resource '%s' cannot be smaller or equal to CONST_COAP_NON_TIMEOUT_S", CONST_COAP_DATA_URI)
elif CONST_INFO_SCAN_PERIOD_S <= CONST_COAP_NON_TIMEOUT_S:
    raise Exception("Scan period of resource '%s' cannot be smaller or equal to CONST_COAP_NON_TIMEOUT_S", CONST_COAP_INFO_URI)

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
    _LOGGER.info("Setting up entry for data entities of device %s with unique ID %s", config[CONF_NAME], config[CONF_ID])
    protocol = await Context.create_client_context()
    sensors = []
    sensors.append(
        CoAPsensorNode(
            "soil-humidity",
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
            "air-humidity",
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
            "sw-version",
            CONST_COAP_INFO_URI,
            config[CONF_NAME],
            None,
            1,
            config[CONF_ID],
        )
    )
    sensors.append(
        CoAPsensorNode(
            "hw-version",
            CONST_COAP_INFO_URI,
            config[CONF_NAME],
            None,
            1,
            config[CONF_ID],
        )
    )
    sensors.append(
        CoAPsensorNode(
            "device-id",
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

    # get device data (confirmable request)
    await sensor_manager.async_get_con_data()
    # get device info (confirmable request)
    await sensor_manager.async_get_con_info()
    
    # request non-confirmable sensor data every CONST_DATA_SCAN_PERIOD_S seconds
    async_track_time_interval(hass, sensor_manager.async_get_non_data, timedelta(seconds=CONST_DATA_SCAN_PERIOD_S))
    _LOGGER.info(" -> Data will be updated every %s seconds", CONST_DATA_SCAN_PERIOD_S)
    # request non-confirmable sensor every CONST_INFO_SCAN_PERIOD_S seconds
    async_track_time_interval(hass, sensor_manager.async_get_non_info, timedelta(seconds=CONST_INFO_SCAN_PERIOD_S))
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

    async def async_get_non_data(self, now=None):
        """Update the device data."""
        try:
            # make sure all data sensors have the same URI 
            for sensor in self._sensors[:4]:
                if sensor.uri != CONST_COAP_DATA_URI:
                    raise Exception("In device %s, not all data sensors have the same URI.")
            request = Message(mtype=NON, code=GET)
            _uri = CONST_COAP_PROTOCOL+self._host+"/"+CONST_COAP_DATA_URI
            _LOGGER.debug("Sending NON GET request to " +  self._name+"/"+CONST_COAP_DATA_URI+"(" + self._host +")")
            request.set_request_uri(uri=_uri)
            # Since this is a non-confirmable request, we need to add a timeout so that we can enter the Exception if we don't get a response from the device.
            # Wihtout this timeout, if the device doesn't send a response, the platform will hang here, and never throw an exception. 
            response = await asyncio.wait_for(self._protocol.request(request).response, timeout=CONST_COAP_NON_TIMEOUT_S)
        except Exception as e:
            _LOGGER.info("-> Exception - Failed to GET '"+CONST_COAP_DATA_URI+"'resource  (NON, mid = "+str(request.mid)+") from "+self._name+"/"+CONST_COAP_DATA_URI)
            _LOGGER.info(e)
        else:
            _LOGGER.debug("-> Received data (mid = "+str(request.mid)+") from "+self._name+"/"+CONST_COAP_DATA_URI+"("+ self._host +")")
            self._sensors[0]._state = round(float(response.payload[0]), self._sensors[0]._round_places)
            self._sensors[1]._state = round(float(response.payload[1]), self._sensors[1]._round_places)
            self._sensors[2]._state = round(float(response.payload[2]), self._sensors[2]._round_places)
            self._sensors[3]._state = round(float(response.payload[3]), self._sensors[3]._round_places)
            _LOGGER.debug("- Soil humidity = "+str(self._sensors[0]._state)+", Battery = "+str(self._sensors[1]._state)+", Air humidity = "+str(self._sensors[2]._state)+", Temperature = "+str(self._sensors[3]._state))
            for sensor in self._sensors[:4]:
                sensor.async_write_ha_state()

    async def async_get_con_data(self, now=None):
        """Update the device data."""
        try:
            # make sure all data sensors have the same URI 
            for sensor in self._sensors[:4]:
                if sensor.uri != CONST_COAP_DATA_URI:
                    _LOGGER.info("In device %s, not all data sensors have the same URI.", sensor.name)
                    raise Exception("In device %s, not all data sensors have the same URI.", sensor.name)
            request = Message(mtype=CON, code=GET)
            _uri = CONST_COAP_PROTOCOL+self._host+"/"+CONST_COAP_DATA_URI
            _LOGGER.debug("Sending CON GET request to " +  self._name+"/"+CONST_COAP_DATA_URI+"(" + self._host +")")
            request.set_request_uri(uri=_uri)
            response = await self._protocol.request(request).response
        except Exception as e:
            _LOGGER.info("-> Exception - Failed to GET '"+CONST_COAP_DATA_URI+"'resource  (CON, mid = "+str(request.mid)+") from "+self._name+"/"+CONST_COAP_DATA_URI)
            _LOGGER.info(e)
        else:
            _LOGGER.debug("-> Received data (mid = "+str(request.mid)+") from "+self._name+"/"+CONST_COAP_DATA_URI+"("+ self._host +")")
            self._sensors[0]._state = round(float(response.payload[0]), self._sensors[0]._round_places)
            self._sensors[1]._state = round(float(response.payload[1]), self._sensors[1]._round_places)
            self._sensors[2]._state = round(float(response.payload[2]), self._sensors[2]._round_places)
            self._sensors[3]._state = round(float(response.payload[3]), self._sensors[3]._round_places)
            _LOGGER.debug("- Soil humidity = "+str(self._sensors[0]._state)+", Battery = "+str(self._sensors[1]._state)+", Air humidity = "+str(self._sensors[2]._state)+", Temperature = "+str(self._sensors[3]._state))
            for sensor in self._sensors[:4]:
                sensor.async_write_ha_state()

    async def async_get_non_info(self, now=None):
        """Update the device info."""
        try:
            #_LOGGER.debug("In  async_get_non_info()...")
            request = Message(mtype=NON, code=GET)
            _uri = CONST_COAP_PROTOCOL+self._host+"/"+CONST_COAP_INFO_URI
            _LOGGER.debug("Sending NON GET request to " +  self._name+"/"+CONST_COAP_INFO_URI+"(" + self._host +")")
            request.set_request_uri(uri=_uri)
            # Since this is a non-confirmable request, we need to add a timeout so that we can enter the Exception if we don't get a response from the device.
            # Wihtout this timeout, if the device doesn't send a response, the platform will hang here, and never throw an exception. 
            response = await asyncio.wait_for(self._protocol.request(request).response, timeout=CONST_COAP_NON_TIMEOUT_S)
        except Exception as e:
            _LOGGER.info("-> Exception - Failed to GET '"+CONST_COAP_INFO_URI+"' resource (NON, mid = "+str(request.mid)+") from "+self._name+"/"+CONST_COAP_INFO_URI)
            _LOGGER.info(e)
        else:
            _LOGGER.debug("-> Received data (mid = "+str(request.mid)+") from "+self._name+"/"+CONST_COAP_INFO_URI+" ("+self._host +")")
            parts = str(response.payload).rsplit(',', 2)
            self._sensors[4]._state = parts[0]
            self._sensors[5]._state = parts[1] if len(parts) > 1 else ""
            self._sensors[6]._state = parts[2] if len(parts) > 2 else ""
            _LOGGER.debug("- FW version is: " + self._sensors[4]._state.strip('b\''))
            _LOGGER.debug("- HW version is: " + self._sensors[5]._state[: -5])
            _LOGGER.debug("- Devie ID is: " + self._sensors[6]._state[: -4])
            self._sensors[4]._state = self._sensors[4]._state.strip('b\'')
            #self._sensors[5]._state = self._sensors[5]._state[: -5]
            self._sensors[6]._state = self._sensors[6]._state[: -5]
            # update each data sensor's info entity
            for sensor in self._sensors[:5]:
                sensor._info = str(response.payload)
            # update sensor manager's info
            self._info = str(response.payload)

    async def async_get_con_info(self, now=None):
        """Update the device info."""
        try:
            #_LOGGER.debug("In  async_get_con_info()...")
            request = Message(mtype=CON, code=GET)
            _uri = CONST_COAP_PROTOCOL+self._host+"/"+CONST_COAP_INFO_URI
            _LOGGER.debug("Sending CON GET request to " +  self._name+"/"+CONST_COAP_INFO_URI+"(" + self._host +")")
            request.set_request_uri(uri=_uri)
            response = await self._protocol.request(request).response
        except Exception as e:
            _LOGGER.info("-> Exception - Failed to GET '"+CONST_COAP_INFO_URI+"' resource (CON, mid = "+str(request.mid)+") from "+self._name+"/"+CONST_COAP_INFO_URI)
            _LOGGER.info(e)
        else:
            _LOGGER.debug("-> Received data (mid = "+str(request.mid)+") from "+self._name+"/"+CONST_COAP_INFO_URI+" ("+self._host +"): "+str(response.payload))
            parts = str(response.payload).rsplit(',', 2)
            self._sensors[4]._state = parts[0]
            self._sensors[5]._state = parts[1] if len(parts) > 1 else ""
            self._sensors[6]._state = parts[2] if len(parts) > 2 else ""
            _LOGGER.debug("- FW version is: " + self._sensors[4]._state.strip('b\''))
            _LOGGER.debug("- HW version is: " + self._sensors[5]._state[: -5])
            _LOGGER.debug("- Device ID is: " + self._sensors[6]._state[: -4])
            self._sensors[4]._state = self._sensors[4]._state.strip('b\'')
            #self._sensors[5]._state = self._sensors[5]._state[: -5]
            self._sensors[6]._state = self._sensors[6]._state[: -5]
            # update each data sensor's info entity
            for sensor in self._sensors[:4]:
                sensor._info = str(response.payload)
            # update sensor manager's info
            self._info = str(response.payload)

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
            model=self._info,
        )

    @property
    def icon(self):
        """Return the icon of the device."""
        if self._sensor_type == "soil-humidity":
            return "mdi:flower"
        elif self._sensor_type == "air-humidity":
            return "mdi:water"
        elif self._sensor_type == "temperature":
            return "mdi:thermometer"
        elif self._sensor_type == "battery":
            return "mdi:battery"
        elif self._sensor_type == "sw-version":
            return "mdi:github"
        elif self._sensor_type == "hw-version":
            return "mdi:information" 
        else:
            return "mdi:cat"

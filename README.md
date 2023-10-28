# ha-coap-integration
Custom components running a CoAP-client to GET/PUT resources held by CoAP-based sensors.

- Each HA CoAp devices implements a CoAp servers and registers itself with Zeroconf as a _ot._udp service.
- HA will run the zeroconf setup every time it discovers a device. If the device has already been setup (device ID has already been registered), it will update its IPv6 address. otherwise, it will prompt the user to give a name to the device.
- Hostname registered with Zeroconf must have the following format: "name-unique_id":
    - "name" cannot have the '-' character
    - unique ID must be uinique (devide ID, uid etc...)
- HA CoAP device must have the following uri:
    1 - temperature (NON, GET)
    2 - light (NON, GET), (CON, PUT)

# HA CoAP

A Home Assistant integration for **ha-coap** plant boards: nRF52840 boards on a Thread network that
measure a plant's soil, their battery, the air's humidity and temperature, and run a small water
pump. Home Assistant talks to them over CoAP, through your Thread border router.

The boards' firmware and the fleet manager app are in
[ha-coap-server](https://github.com/yanntalhouarne/ha-coap-server).

## Installing

Through [HACS](https://hacs.xyz): add this repository as a custom repository (category
*Integration*), install **HA CoAP**, then restart Home Assistant.

Boards are found by themselves: each one registers an `_ot._udp` service (named
`ha-coap-<device ID>`) with the border router, which advertises it to your network. Home Assistant
then offers to add it (Settings → Devices & services): give it a name, such as its plant. You can
also add one by its address (**Add integration → HA CoAP**): the board is asked for its ID, so it
must answer.

## What each board has

| Entity | What it is |
|---|---|
| Soil moisture, Battery, Air humidity, Temperature | Its readings, every 15 minutes (with long-term statistics) |
| Connectivity | Whether it answered its last reading (diagnostic) |
| Firmware, Hardware, Device ID | What it runs and which board it is (diagnostic; also on its device) |
| Run the pump | Runs its pump for its run time |
| Beep | Its buzzer beeps, to find it |
| Pump run time | How long the pump runs, 1 to 9 s (the board keeps it across restarts) |

While a board doesn't answer, its readings show **unavailable** (not their last value) and
Connectivity is off. When the border router gives a board a new address (a new border router does,
for every board), the integration is told by discovery and uses the new one at once.

Download diagnostics (on a board's device page) for what it last said, when something's wrong.

## From version 1

Version 2 is a rewrite: the setup flow, the entities and the CoAP client (no `aiocoap` any more).
The first time Home Assistant starts with it, each board's entry is migrated once:

- Its ID becomes the board's own (`1a2b3c4d`). Version 1 used everything after the name's first
  dash (`coap-1a2b3c4d`).
- Its entities keep their entity IDs, so their history, automations and dashboards are unchanged;
  their names follow Home Assistant's naming ("basil Soil moisture", was "basil.soil-humidity").
- Its device is the same one, now named after the entry.

The migration can't be undone by installing version 1 again (it would find the boards as new ones):
take a backup (or a snapshot of Home Assistant's VM) before updating.

## Developing

The tests use Home Assistant's test kit (Python 3.13):

```
pip install -r requirements_test.txt
pytest
```

`tests/test_live.py` runs against a real board, read only, when given its address:
`HA_COAP_LIVE_HOST=<address> HA_COAP_LIVE_ID=<device ID> pytest tests/test_live.py`.

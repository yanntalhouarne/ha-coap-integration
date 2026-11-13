# HA CoAP

A Home Assistant integration for **ha-coap** plant boards: nRF52840 boards on a Thread network that
measure a plant's soil, their battery, the air's humidity and temperature, and run a small water
pump. Home Assistant talks to them over CoAP, through your Thread border router.

The boards' firmware and the fleet manager app are in
[ha-coap-server](https://github.com/yanntalhouarne/ha-coap-server).

## Installing

Through [HACS](https://hacs.xyz) (called **Marketplace** in newer Home Assistant): add this
repository as a custom repository (category *Integration*), install **HA CoAP**, then restart Home
Assistant. A new version shows there within a few hours of its release; **⋮ → Update information**
looks at once.

Boards are found by themselves: each one registers an `_ot._udp` service (named
`ha-coap-<device ID>`) with the border router, which advertises it to your network. Home Assistant
then offers to add it (Settings → Devices & services): give it a name, such as its plant. You can
also add one by its address (**Add integration → HA CoAP → Add a plant board by its address**): the
board is asked for its ID, so it must answer. **Reconfigure** (on its entry) gives a board a new
address.

## What each board has

| Entity | What it is |
|---|---|
| Soil moisture, Battery, Air humidity, Temperature | Its readings, every 15 minutes, at once when one changed (see Pushes; with long-term statistics) |
| Needs water | On when its soil is under its **Water below** number (30 % by default; 0 turns it off) |
| Next watering | When its soil goes under Water below, at the pace it dried since its last watering (from its samples of the last 2 days) |
| Last watered | When its pump last ran (from here, its button, the fleet manager...) |
| Water used | The water its pump moved (liters, always growing): add it to the **Energy dashboard** under Water consumption. Its run time (the board counts it, whatever started it) times its **Pump flow** number (20 ml/s by default: put the tube in a measuring cup, run the pump, and divide). With the board's newer firmware it shows as each run ends, else at the next reading |
| Run the pump | Runs its pump for its run time |
| Pump running | On while a run started from Home Assistant goes on |
| Beep | Its buzzer beeps, to find it (an "identify" button) |
| Pump run time | How long the pump runs, 1 to 9 s (the board keeps it across restarts). Changed elsewhere (the fleet manager), it follows within seconds with the board's newer firmware, within the hour with older firmware |
| Charging, Plugged in | Its power (newer firmware) |
| Restarted (event) | It restarted, with why: power, reset pin, software, watchdog, CPU lockup, crash. For an automation on crashes |
| Pump button (event) | Its pump ran with its button (seen at the next reading, with how long) |
| Connectivity, Signal, Link quality, Parent router, Thread role | Whether it answers, and its link to the Thread network (diagnostic) |
| Restarts, Last restart, Last restart cause, Soil sensor voltage | Its health (diagnostic; the voltage, for calibrating, is off by default) |
| Firmware, Hardware, Device ID | What it runs and which board it is (diagnostic; also on its device) |

While a board doesn't answer, its readings show **unavailable** (not their last value) and
Connectivity is off. When the border router gives a board a new address (a new border router does,
for every board), the integration is told by discovery and uses the new one at once. A board that
hasn't answered for a day shows in **Repairs**. Its fix asks what happened: a new address (give it),
off for now (no issue until it answers again), or gone (it's removed).

The **Water** action (`ha_coap.water`, on a board's Run the pump button) runs its pump for a given
time, 1 to 9 s, without changing its run time.

Download diagnostics (on a board's device page) for what it last said, when something's wrong.

## The plant card

The integration comes with a dashboard card, **HA CoAP plant**: edit a dashboard → **Add card** →
HA CoAP plant (or search "plant"), and choose the plant board. Nothing else to install: the
integration serves it, and an update of the integration updates it (reload the page once).

```yaml
type: custom:ha-coap-plant-card
device: <the board's device: chosen in the card's editor>
name: Basil   # optional: else the device's name
```

It shows how the plant is (happy, thirsty, watering, soaked, not answering) as its color, its soil
as a gauge with its Water below target marked, when it needs water next and when it was last
watered, the water used, its soil over the last 2 days (with the target), its battery (and its
days left, or charging), temperature and air. Then its pump: the run time as a slider (it asks
"Change the run time to 6 s?" before changing it) and **Water** (tap twice: once to ask, once to
water). With the fleet manager connected: its water
tank (with **Refilled**, tapped twice too) and its care tasks that are due (tap one once it's
done; the next one coming shows under them). Tapping a value opens its entity. It follows the
theme (light, dark) and fits a phone.

## Pushes, and gaps filled in

Newer firmware keeps its last samples (`hist`: 192, two days at one per 15 minutes) and pushes the
new ones when its soil or battery moved by 5 % or more, or its power changed: a watering, a board
plugged in show at once. It also pushes when its pump's run time changes, and when a pump run
ends: the integration reads its `diag` again (the run time, the water used). The integration reads the samples at each reading and registers for the
pushes, but only on firmware with room for Home Assistant beside the fleet manager (its `diag`
says `observers=2` or more); on firmware with one place, registering would take the fleet
manager's.

The samples also fill in the **statistics** of the hours Home Assistant missed (while the board
couldn't be reached, or Home Assistant wasn't running): only hours with no statistics are written.
Home Assistant's state history can't be written after the fact; its statistics (hourly mean, min,
max: the statistics graphs, and history over 10 days) can.

## Firmware updates (the fleet manager)

With the fleet manager (the ha-coap-server app), each board has a **Firmware** update in
Settings → Updates: what it runs against the release image, and **Install** to update it over the
air. In the fleet manager: Settings, Other devices, make a link with **For the HA CoAP
integration** ticked: it shows an address and a key. In Home Assistant: **Add integration → HA
CoAP → Connect the fleet manager**, and paste them. That link only lets Home Assistant read the
firmware and update a board to the release image, read the plants' water tanks, battery
forecasts and care tasks, and mark a tank refilled or a care task done, nothing else.

## Water tanks, battery forecasts, care (the fleet manager)

The same connection gives each plant, on its board's device (with the fleet manager's version of
November 2026 or later):

| Entity | What it is |
|---|---|
| Water tank | How full its tank is (%), with the ml left: the fleet manager counts every pump run against it, Home Assistant's too. Only for a plant whose tank's size is set in the fleet manager (its page, Watering) |
| Water tank days left | How long its tank lasts at the pace it was used over the last two weeks (unknown until then) |
| Water tank low | On at 20 % left |
| Water tank refilled | Press it once the tank is full again: it's counted from now |
| Battery days left | When its battery runs flat, from its trend over two weeks (unknown on USB power, or without a clear trend yet) |
| Care due | How many of its care tasks are due (with them all, for its card). Only for a plant with care tasks |

And one to-do list, **Plant care**, of the plants' care tasks (fertilize, repot...: added in the
fleet manager, on each plant's page). One that's due is to do; tick it once done, and it's checked
until it's due again, its number of days later. The fleet manager is read every 5 minutes.

A second blueprint says when a tank is low (a notification in Home Assistant, and your own actions,
such as a notification to your phone):
`https://github.com/yanntalhouarne/ha-coap-integration/blob/master/blueprints/automation/ha_coap/tank_low.yaml`
(Settings → Automations → Blueprints → Import).

## Watering automatically

A blueprint waters a plant when it needs water, between two times of the day, and not again
within so many hours (for the water to reach the sensor):

[![Open your Home Assistant instance and show the blueprint import dialog.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fyanntalhouarne%2Fha-coap-integration%2Fblob%2Fmaster%2Fblueprints%2Fautomation%2Fha_coap%2Fwater_when_dry.yaml)

Or Settings → Automations → Blueprints → Import, with
`https://github.com/yanntalhouarne/ha-coap-integration/blob/master/blueprints/automation/ha_coap/water_when_dry.yaml`.
Don't use it on a plant the fleet manager waters automatically too.

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

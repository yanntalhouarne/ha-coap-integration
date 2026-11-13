"""One board: its address, what it last said, and the requests to it.

Every UPDATE_INTERVAL: its readings ('data'), its health ('diag'), and the samples it kept since
the last time ('hist', newer firmware). Its info and pump run time every INFO_EVERY times. Where
the firmware has room for Home Assistant beside the fleet manager ('diag' observers=2 or more),
reading 'hist' also registers for its pushes: a sample that changed (a watering, a board
plugged in) shows at once, and so do its pump's run time set elsewhere and the water a pump run
gave, whoever started it. The samples also fill in the statistics of hours Home Assistant
missed, and tell when the plant will need water.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_ID, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import coap, plant, statistics
from .const import DOMAIN, INFO_EVERY, QUIET_WHILE_OFF, UPDATE_INTERVAL, board_id, clean_host

_LOGGER = logging.getLogger(__name__)

ERRORS = (TimeoutError, OSError, coap.CoapError)
KEEP_SAMPLES_S = 48 * 3600   # the samples kept here: enough for the forecast, as many as the board keeps
HISTORY_READS = 4            # answers read at most at once (54 samples each)
UNREACHABLE_ISSUE_S = 86400  # not answering for this long: a Repairs issue (its address may be dead)
DEFAULT_WATER_BELOW = 30     # % of soil: the default target (Water below)
DEFAULT_PUMP_FLOW = 20.0     # ml/s: the default pump flow (as the fleet manager's)

type HaCoapConfigEntry = ConfigEntry[HaCoapCoordinator]


@dataclass
class BoardState:
    """What the board last said. 'info', 'pump_s' and 'diag' are None until it answered them."""

    data: coap.Data
    info: coap.Info | None = None
    pump_s: int | None = None        # its pump's run time (s), 1 to 9
    diag: coap.Diagnostics | None = None
    power: str = ""                  # battery, usb, charging, charged ('diag', or its newest sample)
    pump_running: bool = False       # a run started from here, until its run time is over
    last_watered: float | None = None  # when its pump last ran (seen from here)


class HaCoapCoordinator(DataUpdateCoordinator[BoardState]):
    config_entry: HaCoapConfigEntry

    def __init__(self, hass: HomeAssistant, entry: HaCoapConfigEntry, observer: coap.Observer | None = None) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=entry.title or entry.data[CONF_NAME],
                         update_interval=UPDATE_INTERVAL)
        self.host = clean_host(entry.data[CONF_HOST])
        self.device_id: str = entry.data[CONF_ID]
        self.observer = observer
        self.samples: list[tuple[float, coap.Sample]] = []  # its samples of the last 48 h, oldest first
        self.soil_points: list[tuple[float, float]] = []    # (time, soil %): the samples', or the readings'
        self.pumped_ms = 0           # its pump's run time seen since Home Assistant started (ms), for the water used
        self.water_below: float = DEFAULT_WATER_BELOW  # set by its Water below number
        self.pump_flow: float = DEFAULT_PUMP_FLOW      # set by its Pump flow number
        self.restart_listeners: list[Callable[[coap.Diagnostics], None]] = []
        self.button_listeners: list[Callable[[int], None]] = []
        self._updates = 0
        self._hist_boot: int | None = None
        self._hist_next = 0
        self._no_hist = False
        self._seen: set[tuple[int, int]] = set()  # (boot, number) of the samples kept
        self._prev_diag: coap.Diagnostics | None = None
        self._down_since: float | None = None
        self._issue_cleared = False
        self._stop_running = None
        self._watered_at: float | None = None  # when its pump last ran, seen from here
        self._pump_lock = asyncio.Lock()  # its run time is changed from here: read it after

    # ------------------------------------------------------------ reading the board

    async def _async_update_data(self) -> BoardState:
        now = time.time()
        try:
            data = coap.parse_data(await coap.get(self.host, "data"))
        except ERRORS as err:
            self._not_answering(now)
            raise UpdateFailed(f"No answer from {self.name} ({self.host}): {err}") from err
        self._answering()
        previous = self.data
        info = previous.info if previous else None
        pump_s = previous.pump_s if previous else None
        rarely = self._updates % INFO_EVERY == 0
        if info is None or rarely:
            info = await self._read_info() or info
        self._updates += 1
        diag = await self._read_diag()
        if diag is not None and not self._diag_changes(diag):
            diag = None  # older than one a push had read: that one stands
        if diag is not None and diag.pump_s is not None:
            pump_s = diag.pump_s  # newer firmware tells it at every reading
        elif pump_s is None or rarely:
            pump_s = await self.read_pump_s() or pump_s
        diag = diag or (self.data.diag if self.data else None)  # (a push may have read a newer one meanwhile)
        added = await self._read_history(diag)
        if self._no_hist:
            self._keep_point(now, data.soil_moisture)
        if added and statistics.gap_hours(added, now):
            self.hass.async_create_task(statistics.async_fill_gaps(self.hass, self.device_id, added, now),
                                        f"{DOMAIN} statistics of {self.name}")
        return BoardState(data, info, pump_s, diag, diag.power if diag and diag.power else (previous.power if previous else ""),
                          previous.pump_running if previous else False, self._last_watered(previous))

    def _last_watered(self, previous: BoardState | None) -> float | None:
        return self._watered_at or (previous.last_watered if previous else None)

    async def _read_info(self) -> coap.Info | None:
        """Its info, noted on its device (firmware, hardware). None when it didn't answer."""
        try:
            info = coap.parse_info(await coap.get(self.host, "info"))
        except ERRORS as err:
            _LOGGER.debug("%s: no 'info' (%s)", self.name, err)
            return None
        expected = board_id(self.device_id)  # also in an ID kept from version 1, such as "coap-1a2b3c4d"
        if expected and info.device_id and info.device_id != expected:
            # Another board answers at this address: its readings aren't this plant's
            raise UpdateFailed(f"{self.host} is board {info.device_id} now, not {self.device_id}: "
                               f"it will be found again at its new address")
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, self.device_id)})
        if device and (device.sw_version, device.hw_version) != (info.fw_version, info.hw_version or None):
            registry.async_update_device(device.id, sw_version=info.fw_version, hw_version=info.hw_version or None)
        return info

    async def read_pump_s(self) -> int | None:
        try:
            payload = await coap.get(self.host, "pumpdc")
        except ERRORS as err:
            _LOGGER.debug("%s: no 'pumpdc' (%s)", self.name, err)
            return None
        return payload[0] if payload and 1 <= payload[0] <= 9 else None

    async def _read_diag(self) -> coap.Diagnostics | None:
        try:
            return coap.parse_diag(await coap.get(self.host, "diag"))
        except ERRORS as err:
            _LOGGER.debug("%s: no 'diag' (%s)", self.name, err)  # older firmware
            return None

    def _diag_changes(self, diag: coap.Diagnostics) -> bool:
        """A restart and its cause, its button's pump runs, its pump's run time: from what changed.
        False for an answer older than the last one (a reading's, overtaken by a push's read): it
        would count its runs again."""
        before = self._prev_diag
        restarted = before is not None and diag.boots != before.boots
        if before is not None and not restarted and diag.uptime_s < before.uptime_s:
            return False
        self._prev_diag = diag
        if restarted:
            for listener in list(self.restart_listeners):
                listener(diag)
        if diag.button_s is not None and before is not None and before.button_s is not None:
            ran = diag.button_s if restarted else diag.button_s - before.button_s
            if ran > 0:
                for listener in list(self.button_listeners):
                    listener(ran)
                if diag.pump_ms is None:  # older firmware: its button's runs are what it tells
                    self._pumped(ran * 1000)
        if diag.pump_ms is not None and before is not None and before.pump_ms is not None:
            ran_ms = diag.pump_ms if restarted else diag.pump_ms - before.pump_ms
            if ran_ms > 0:
                self._pumped(ran_ms)
        return True

    def _pumped(self, ms: int, when: float | None = None) -> None:
        self.pumped_ms += ms
        self._watered_at = when or time.time()

    async def _read_history(self, diag: coap.Diagnostics | None) -> list[tuple[float, coap.Sample]]:
        """The samples it kept since the last read (all of them at the first), observing it for
        its pushes when there's room. Returns the new ones."""
        if self._no_hist:
            return []
        observe = self.observer is not None and diag is not None and diag.observers >= 2
        added = []
        start = self._hist_next if self._hist_boot is not None else 0
        for read in range(HISTORY_READS):
            query = (f"from={start}",)
            try:
                if observe and read == 0:
                    code, payload, _ = await self.observer.register(self.host, "hist", query)
                else:
                    code, payload = await coap.request(self.host, "hist", coap.GET, query=query)
            except ERRORS as err:
                _LOGGER.debug("%s: no 'hist' (%s)", self.name, err)
                break
            if code == coap.NOT_FOUND:
                self._no_hist = True  # older firmware: its readings are the samples
                break
            if code != coap.CONTENT:
                break
            history = coap.parse_history(payload)
            if self._hist_boot is not None and history.boot != self._hist_boot and history.first > 0 and start > 0:
                self._hist_boot, start = history.boot, 0  # it restarted: its numbers start again, read from its first
                continue
            added += self._keep(history, time.time())
            start = history.read_up_to
            if history.next <= history.read_up_to:
                break
        return added

    def _keep(self, history: coap.History, received_at: float) -> list[tuple[float, coap.Sample]]:
        """Keeps the samples not seen yet; returns them."""
        self._hist_boot, self._hist_next = history.boot, history.read_up_to
        added = []
        for sample in history.samples:
            if (history.boot, sample.seq) in self._seen:
                continue
            self._seen.add((history.boot, sample.seq))
            when = min(history.time_of(sample, received_at), received_at)
            added.append((when, sample))
        if added:
            self.samples = sorted(self.samples + added, key=lambda s: s[0])
            cutoff = received_at - KEEP_SAMPLES_S
            self.samples = [s for s in self.samples if s[0] >= cutoff]
            self._seen = {k for k in self._seen if k[0] == history.boot} or self._seen
            self.soil_points = [(t, s.soil_moisture) for t, s in self.samples]
        return added

    def _keep_point(self, when: float, soil: int) -> None:
        self.soil_points = [p for p in self.soil_points if p[0] >= when - KEEP_SAMPLES_S] + [(when, soil)]

    @callback
    def on_push(self, payload: bytes) -> None:
        """The board pushed its new samples (a watering, plugged in...): its readings at once. A
        push without new samples: something changed (its pump's run time, set in the fleet
        manager; a pump run ended, whoever started it): its 'diag' read again."""
        try:
            history = coap.parse_history(payload)
        except coap.CoapError:
            return
        if self._hist_boot is not None and history.boot != self._hist_boot:
            self._hist_next = 0  # it restarted: read again from its first at the next reading
        added = self._keep(history, time.time())
        if self.data is None:
            return
        if not added:
            self.config_entry.async_create_background_task(self.hass, self._after_change(),
                                                           f"{DOMAIN} change on {self.name}")
            return
        newest = added[-1][1]
        data = coap.Data(newest.soil_moisture, newest.battery, newest.air_humidity, newest.temperature)
        self.async_set_updated_data(replace(self.data, data=data, power=newest.power or self.data.power))

    async def _after_change(self) -> None:
        """Its 'diag' again: its pump's run time, and the water a pump run just gave ('pumpdc' with
        firmware without pump_s in 'diag'). After its changes from here (not one of the Water
        action's, which it puts back)."""
        async with self._pump_lock:
            diag = await self._read_diag()
            pump_s = diag.pump_s if diag is not None and diag.pump_s is not None else await self.read_pump_s()
        if self.data is None:
            return
        state = self.data
        if diag is not None and self._diag_changes(diag):
            state = replace(state, diag=diag, power=diag.power or state.power, last_watered=self._last_watered(state))
        if pump_s is not None:
            state = replace(state, pump_s=pump_s)
        if state != self.data:
            self.async_set_updated_data(state)

    # ------------------------------------------------------------ when it doesn't answer

    def _issue_id(self) -> str:
        return f"unreachable_{self.config_entry.entry_id}"

    def _not_answering(self, now: float) -> None:
        if self.config_entry.data.get(QUIET_WHILE_OFF):
            return  # said to be off (Repairs): not an issue until it has answered again
        if self._down_since is None:
            self._down_since = now
        elif now - self._down_since >= UNREACHABLE_ISSUE_S:
            ir.async_create_issue(self.hass, DOMAIN, self._issue_id(), is_fixable=True, severity=ir.IssueSeverity.WARNING,
                                  translation_key="unreachable", data={"entry_id": self.config_entry.entry_id},
                                  translation_placeholders={"name": self.name, "host": self.host})

    def _answering(self) -> None:
        """It answers: no issue (also one raised before a reload, such as after its Repairs fix),
        and one again if it stops answering for a day."""
        if self.config_entry.data.get(QUIET_WHILE_OFF):
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={k: v for k, v in self.config_entry.data.items() if k != QUIET_WHILE_OFF})
        if self._down_since is not None or not self._issue_cleared:
            self._down_since = None
            self._issue_cleared = True
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id())

    # ------------------------------------------------------------ the plant

    @property
    def needs_water_at(self) -> float | None:
        return plant.needs_water_at(self.soil_points, self.water_below, time.time())

    # ------------------------------------------------------------ requests from Home Assistant

    async def put(self, path: str, payload: bytes) -> bytes:
        """A PUT to the board, for a button or a setting. Raises HomeAssistantError when it
        doesn't answer (the page shows it)."""
        try:
            return await coap.put(self.host, path, payload)
        except ERRORS as err:
            raise HomeAssistantError(f"{self.name} didn't answer: {err}") from err

    async def set_pump_s(self, seconds: int) -> int:
        """Sets its pump's run time (1 to 9 s); returns the one it uses now."""
        async with self._pump_lock:
            answer = await self.put("pumpdc", str(seconds).encode())
        value = answer[0] if answer and 1 <= answer[0] <= 9 else seconds
        if self.data is not None:
            self.async_set_updated_data(replace(self.data, pump_s=value))
        return value

    async def water(self, seconds: int | None = None) -> None:
        """Runs its pump: for its run time, or for 'seconds' (1 to 9) without changing it (its run
        time is set for this run, then put back: the board times a run when it starts)."""
        run_time = self.data.pump_s if self.data and self.data.pump_s else None
        async with self._pump_lock:
            if seconds is not None and seconds != run_time:
                await self.put("pumpdc", str(seconds).encode())
            await self.put("pump", b"1")
            if seconds is not None and run_time is not None and seconds != run_time:
                await self.put("pumpdc", str(run_time).encode())
        ran_s = seconds or run_time or 1
        if self._prev_diag is None or self._prev_diag.pump_ms is None:
            self._pumped(ran_s * 1000)  # firmware without pump_ms: counted here
        else:
            self._watered_at = time.time()
        if self.data is not None:
            self.async_set_updated_data(replace(self.data, pump_running=True, last_watered=time.time()))
        if self._stop_running is not None:
            self._stop_running()
        self._stop_running = async_call_later(self.hass, ran_s + 1, self._pump_done)

    @callback
    def _pump_done(self, _now) -> None:
        self._stop_running = None
        if self.data is not None and self.data.pump_running:
            self.async_set_updated_data(replace(self.data, pump_running=False))

    async def async_shutdown(self) -> None:
        if self._stop_running is not None:
            self._stop_running()
            self._stop_running = None
        await super().async_shutdown()

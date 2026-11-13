"""
FaderDisplay: renders PipeWire/PulseAudio sink volumes as vertical faders
spread across columns of Stream Deck keys.

The plugin keeps one background thread listening for PulseAudio sink events;
whenever a watched sink's volume or mute state changes, every registered
Segment action re-renders its slice of the fader.
"""

import math
import subprocess
import threading
import time
from array import array

import pulsectl
from loguru import logger as log

from src.backend.PluginManager.PluginBase import PluginBase
from src.backend.PluginManager.ActionHolder import ActionHolder
from src.backend.PluginManager.ActionInputSupport import ActionInputSupport
from src.backend.DeckManagement.InputIdentifier import Input

from .actions.Segment import Segment
from .actions.MediaKey import MediaKey


def _amp_to_frac(amp: float) -> float:
    """
    Map a peak amplitude to a fader-column fraction (0..1).
    0 dBFS lands on the 100%-volume line (2/3 of the column, matching
    max-volume=150), with -42 dB at the bottom and +6 dB of overdrive
    headroom in the top third.
    """
    if amp < 1e-4:
        return 0.0
    db = 20 * math.log10(amp)
    if db <= 0:
        return max(0.0, (db + 42) / 42) * (2 / 3)
    return 2 / 3 + min(db / 6, 1.0) / 3


class _MeterState:
    __slots__ = ("raw_max", "last_data", "level", "hold", "hold_ts", "proc", "wanted")

    def __init__(self):
        # all level fields are [left, right] amplitudes
        self.raw_max = [0.0, 0.0]  # peaks since the display loop last consumed them
        self.last_data = 0.0
        self.level = [0.0, 0.0]    # displayed bar level (post-ballistics)
        self.hold = [0.0, 0.0]     # peak-hold markers
        self.hold_ts = [0.0, 0.0]
        self.proc = None
        self.wanted = True


class FaderDisplay(PluginBase):
    def __init__(self):
        super().__init__()

        self._segments = []
        self._media_keys = []
        self._lock = threading.Lock()
        self.sink_state: dict[str, tuple[int, bool]] = {}  # name -> (volume %, muted)
        self.media_status: str | None = None  # "Playing"/"Paused"/"Stopped", None = no player

        self._meters: dict[str, _MeterState] = {}  # meter-target -> state
        # target -> ((bar_l, bar_r), (hold_l, hold_r)) as column fractions, for Segment.render
        self.meter_display: dict[str, tuple] = {}
        self._meter_display_started = False

        self.segment_holder = ActionHolder(
            plugin_base=self,
            action_base=Segment,
            action_id_suffix="Segment",
            action_name="Fader Segment",
            action_support={
                Input.Key: ActionInputSupport.SUPPORTED,
                Input.Dial: ActionInputSupport.UNSUPPORTED,
                Input.Touchscreen: ActionInputSupport.UNSUPPORTED,
            },
        )
        self.add_action_holder(self.segment_holder)

        self.media_key_holder = ActionHolder(
            plugin_base=self,
            action_base=MediaKey,
            action_id_suffix="MediaKey",
            action_name="Media Key",
            action_support={
                Input.Key: ActionInputSupport.SUPPORTED,
                Input.Dial: ActionInputSupport.UNSUPPORTED,
                Input.Touchscreen: ActionInputSupport.UNSUPPORTED,
            },
        )
        self.add_action_holder(self.media_key_holder)

        self.register()

        threading.Thread(target=self._monitor, daemon=True, name="fader-display-pulse").start()
        threading.Thread(target=self._media_monitor, daemon=True, name="fader-display-media").start()

    # Segment registry

    def add_segment(self, segment) -> None:
        with self._lock:
            if segment not in self._segments:
                self._segments.append(segment)
        self._sync_meters()

    def remove_segment(self, segment) -> None:
        with self._lock:
            if segment in self._segments:
                self._segments.remove(segment)
        self._sync_meters()

    def get_sink_state(self, name: str):
        return self.sink_state.get(name)

    def add_media_key(self, key) -> None:
        with self._lock:
            if key not in self._media_keys:
                self._media_keys.append(key)

    def remove_media_key(self, key) -> None:
        with self._lock:
            if key in self._media_keys:
                self._media_keys.remove(key)

    # Pulse monitoring

    def _refresh(self, pulse) -> bool:
        state = {}
        for sink in pulse.sink_list():
            volume = round(pulse.volume_get_all_chans(sink) * 100)
            state[sink.name] = (volume, bool(sink.mute))
        if state != self.sink_state:
            self.sink_state = state
            return True
        return False

    def _notify(self) -> None:
        with self._lock:
            segments = list(self._segments)
        for segment in segments:
            try:
                segment.render()
            except Exception as e:
                log.error(f"FaderDisplay: segment render failed: {e}")

    def _monitor(self) -> None:
        def on_event(ev):
            raise pulsectl.PulseLoopStop

        while True:
            try:
                with pulsectl.Pulse("fader-display-monitor") as pulse:
                    if self._refresh(pulse):
                        self._notify()
                    pulse.event_mask_set("sink")
                    pulse.event_callback_set(on_event)
                    while True:
                        pulse.event_listen(timeout=10)
                        if self._refresh(pulse):
                            self._notify()
            except Exception as e:
                log.warning(f"FaderDisplay: pulse monitor reconnecting: {e}")
                time.sleep(3)

    # Media (MPRIS) monitoring

    def _notify_media(self) -> None:
        with self._lock:
            keys = list(self._media_keys)
        for key in keys:
            try:
                key.render()
            except Exception as e:
                log.error(f"FaderDisplay: media key render failed: {e}")

    def _media_monitor(self) -> None:
        # playerctl --follow prints the current status immediately, then one
        # line per change; an empty line means no player is available.
        while True:
            try:
                # cwd="/": flatpak-spawn --host replays the caller's cwd on the host,
                # and the sandbox's /app/... paths don't exist there
                proc = subprocess.Popen(
                    ["flatpak-spawn", "--host", "playerctl", "--follow", "status"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd="/",
                )
                for line in proc.stdout:
                    status = line.strip() or None
                    if status != self.media_status:
                        self.media_status = status
                        self._notify_media()
                proc.wait()
            except Exception as e:
                log.warning(f"FaderDisplay: media monitor restarting: {e}")
            if self.media_status is not None:
                self.media_status = None
                self._notify_media()
            time.sleep(5)

    # Level metering
    #
    # Each segment may carry a "meter-target" setting naming a PipeWire node to
    # meter: a loopback output stream ("media_channel.out", captured with
    # pw-record, post-fader) or a hardware sink monitor ("<sink>.monitor",
    # captured with parec - pw-record can't tap hardware sink monitors and
    # parec can't tap stream nodes, so both are needed). Virtual-sink monitors
    # are silent on this PipeWire, hence the .out stream taps.

    METER_INTERVAL = 1 / 15  # display refresh period (s)
    METER_RELEASE = 0.86     # bar decay factor per tick (fast attack, slow release)
    METER_HOLD_S = 1.2     # peak-hold time before the marker collapses
    METER_STALE_S = 0.5    # no samples for this long -> treat as silence

    def get_meter_display(self, target: str) -> tuple:
        return self.meter_display.get(target, ((0.0, 0.0), (0.0, 0.0)))

    def _sync_meters(self) -> None:
        """Start/stop capture threads to match the registered segments."""
        with self._lock:
            segments = list(self._segments)
        wanted = set()
        for seg in segments:
            try:
                target = seg.get_settings().get("meter-target")
            except Exception:
                target = None
            if target:
                wanted.add(target)

        for target in wanted:
            if target not in self._meters:
                self._meters[target] = _MeterState()
                threading.Thread(target=self._meter_reader, args=(target,),
                                 daemon=True, name=f"meter-{target[-20:]}").start()
                if not self._meter_display_started:
                    self._meter_display_started = True
                    threading.Thread(target=self._meter_display_loop,
                                     daemon=True, name="meter-display").start()

        for target in list(self._meters):
            if target not in wanted:
                state = self._meters.pop(target)
                state.wanted = False
                if state.proc is not None:
                    try:
                        state.proc.kill()
                    except Exception:
                        pass
                self.meter_display.pop(target, None)

    def _meter_reader(self, target: str) -> None:
        """Hold one capture pipe open and accumulate stereo peaks."""
        state = self._meters.get(target)
        if state is None:
            return
        if target.endswith(".monitor"):
            cmd = ["flatpak-spawn", "--host", "parec", f"--device={target}",
                   "--format=float32le", "--rate=8000", "--channels=2",
                   "--raw", "--latency-msec=50"]
        else:
            cmd = ["flatpak-spawn", "--host", "pw-record", "--target", target,
                   "--format", "f32", "--rate", "8000", "--channels", "2", "-"]
        while state.wanted:
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, cwd="/")
                state.proc = proc
                while state.wanted:
                    chunk = proc.stdout.read(3200)  # 50 ms of stereo f32 @ 8 kHz
                    if not chunk:
                        break
                    vals = array("f", chunk[:len(chunk) // 8 * 8])
                    if not vals:
                        continue
                    peak_l = max(abs(v) for v in vals[0::2])
                    peak_r = max(abs(v) for v in vals[1::2])
                    with self._lock:
                        state.raw_max[0] = max(state.raw_max[0], peak_l)
                        state.raw_max[1] = max(state.raw_max[1], peak_r)
                        state.last_data = time.time()
            except Exception as e:
                log.warning(f"FaderDisplay: meter capture for {target} restarting: {e}")
            if state.wanted:
                time.sleep(3)

    def _meter_display_loop(self) -> None:
        renders = 0
        render_time = 0.0
        last_report = time.time()
        while True:
            time.sleep(self.METER_INTERVAL)
            now = time.time()
            for target, state in list(self._meters.items()):
                with self._lock:
                    raw = list(state.raw_max)
                    state.raw_max[0] = state.raw_max[1] = 0.0
                    stale = now - state.last_data > self.METER_STALE_S
                if stale:
                    raw = [0.0, 0.0]
                bars, holds = [], []
                for ch in (0, 1):
                    level = max(raw[ch], state.level[ch] * self.METER_RELEASE)
                    state.level[ch] = level if level >= 1e-4 else 0.0
                    if raw[ch] > state.hold[ch]:
                        state.hold[ch] = raw[ch]
                        state.hold_ts[ch] = now
                    elif now - state.hold_ts[ch] > self.METER_HOLD_S:
                        state.hold[ch] = state.level[ch]
                    bars.append(_amp_to_frac(state.level[ch]))
                    holds.append(_amp_to_frac(state.hold[ch]))
                self.meter_display[target] = (tuple(bars), tuple(holds))

            with self._lock:
                segments = list(self._segments)
            for seg in segments:
                try:
                    px = seg.meter_px()
                    if px is None or px == getattr(seg, "_last_meter_px", None):
                        continue
                    seg._last_meter_px = px
                    t0 = time.time()
                    seg.render()
                    render_time += time.time() - t0
                    renders += 1
                except Exception as e:
                    log.error(f"FaderDisplay: meter render failed: {e}")

            if now - last_report >= 10:
                if renders:
                    log.debug(f"FaderDisplay meter: {renders} key renders in "
                              f"{now - last_report:.0f}s, avg {1000 * render_time / renders:.1f} ms")
                renders = 0
                render_time = 0.0
                last_report = now

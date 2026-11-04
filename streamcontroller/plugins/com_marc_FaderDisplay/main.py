"""
FaderDisplay: renders PipeWire/PulseAudio sink volumes as vertical faders
spread across columns of Stream Deck keys.

The plugin keeps one background thread listening for PulseAudio sink events;
whenever a watched sink's volume or mute state changes, every registered
Segment action re-renders its slice of the fader.
"""

import subprocess
import threading
import time

import pulsectl
from loguru import logger as log

from src.backend.PluginManager.PluginBase import PluginBase
from src.backend.PluginManager.ActionHolder import ActionHolder
from src.backend.PluginManager.ActionInputSupport import ActionInputSupport
from src.backend.DeckManagement.InputIdentifier import Input

from .actions.Segment import Segment
from .actions.MediaKey import MediaKey


class FaderDisplay(PluginBase):
    def __init__(self):
        super().__init__()

        self._segments = []
        self._media_keys = []
        self._lock = threading.Lock()
        self.sink_state: dict[str, tuple[int, bool]] = {}  # name -> (volume %, muted)
        self.media_status: str | None = None  # "Playing"/"Paused"/"Stopped", None = no player

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

    def remove_segment(self, segment) -> None:
        with self._lock:
            if segment in self._segments:
                self._segments.remove(segment)

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

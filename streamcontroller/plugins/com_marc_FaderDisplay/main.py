"""
FaderDisplay: renders PipeWire/PulseAudio sink volumes as vertical faders
spread across columns of Stream Deck keys.

The plugin keeps one background thread listening for PulseAudio sink events;
whenever a watched sink's volume or mute state changes, every registered
Segment action re-renders its slice of the fader.
"""

import threading
import time

import pulsectl
from loguru import logger as log

from src.backend.PluginManager.PluginBase import PluginBase
from src.backend.PluginManager.ActionHolder import ActionHolder
from src.backend.PluginManager.ActionInputSupport import ActionInputSupport
from src.backend.DeckManagement.InputIdentifier import Input

from .actions.Segment import Segment


class FaderDisplay(PluginBase):
    def __init__(self):
        super().__init__()

        self._segments = []
        self._lock = threading.Lock()
        self.sink_state: dict[str, tuple[int, bool]] = {}  # name -> (volume %, muted)

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

        self.register()

        threading.Thread(target=self._monitor, daemon=True, name="fader-display-pulse").start()

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

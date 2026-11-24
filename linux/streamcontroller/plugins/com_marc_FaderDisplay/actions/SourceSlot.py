from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from .AppSlot import AppSlot


class SourceSlot(AppSlot):
    """
    One key of a discovered-input fader column on the Input page.

    Inputs are non-monitor sources (microphones, line-ins) discovered by the
    plugin; the optional config file's "inputs" list sets order, labels and
    colors, exactly like "channels" does for the Output page.

    Settings (page JSON):
      slot:        column index 0..3
      glyph:       "up" | "down" | "mic" (mic = mute toggle + mic glyph)
      range-low/range-high, text, max-volume: as for Segment
    """

    def create_event_assigners(self):
        self.add_event_assigner(EventAssigner(
            id="source-slot",
            ui_label="Input Slot",
            default_events=[Input.Key.Events.DOWN],
            callback=self._on_press,
        ))

    def on_ready(self):
        self.plugin_base.add_source_slot(self)
        self.render()

    def on_removed_from_cache(self):
        self.plugin_base.remove_source_slot(self)

    def _source(self):
        return self.plugin_base.get_source(int(self.get_settings().get("slot", 0)))

    def _slot_data(self, want_icon: bool = True):
        source = self._source()
        if source is None:
            return None
        state = self.plugin_base.get_source_state(source["name"])
        if state is None:
            return None
        volume, muted = state
        label = source["label"]
        return {
            "volume": volume,
            "muted": muted,
            "color": source["color"],
            "meter": self.plugin_base.get_meter_display(source["meter_target"]),
            "label": label if len(label) <= 9 else label[:8] + "…",
            "icon": None,
            "accent": source.get("accent", False),
        }

    def _on_press(self, event):
        source = self._source()
        if source is None:
            return
        glyph = self.get_settings().get("glyph")
        if glyph == "up":
            self.plugin_base.adjust_source_volume(source["name"], +10)
        elif glyph == "down":
            self.plugin_base.adjust_source_volume(source["name"], -10)
        elif glyph in ("mic", "mute"):
            self.plugin_base.toggle_source_mute(source["name"])

from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from .AppSlot import AppSlot


class ChannelSlot(AppSlot):
    """
    One key of a discovered-channel fader column on the Mixer page.

    Channels are sinks discovered by the plugin (names ending "_channel",
    plus anything pinned in the optional config file, which also sets order,
    labels and colors); slot "master" is the current default output device.

    Settings (page JSON):
      slot:        column index 0.. or "master"
      glyph:       "up" | "down" | "mute" (also selects the press behavior)
      range-low/range-high, text, max-volume: as for Segment
    """

    def create_event_assigners(self):
        self.add_event_assigner(EventAssigner(
            id="channel-slot",
            ui_label="Channel Slot",
            default_events=[Input.Key.Events.DOWN],
            callback=self._on_press,
        ))

    def on_ready(self):
        self.plugin_base.add_channel_slot(self)
        self.render()

    def on_removed_from_cache(self):
        self.plugin_base.remove_channel_slot(self)

    def _channel(self):
        slot = self.get_settings().get("slot", 0)
        if slot == "master":
            return self.plugin_base.get_master_channel()
        return self.plugin_base.get_channel(int(slot))

    def _slot_data(self, want_icon: bool = True):
        channel = self._channel()
        if channel is None:
            return None
        state = self.plugin_base.get_sink_state(channel["name"])
        if state is None:
            return None
        volume, muted = state
        return {
            "volume": volume,
            "muted": muted,
            "color": channel["color"],
            "meter": self.plugin_base.get_meter_display(channel["meter_target"]),
            "label": channel["label"],
            "icon": None,
        }

    def _on_press(self, event):
        channel = self._channel()
        if channel is None:
            return
        glyph = self.get_settings().get("glyph")
        if glyph == "up":
            self.plugin_base.adjust_sink_volume(channel["name"], +10)
        elif glyph == "down":
            self.plugin_base.adjust_sink_volume(channel["name"], -10)
        elif glyph == "mute":
            self.plugin_base.toggle_sink_mute(channel["name"])

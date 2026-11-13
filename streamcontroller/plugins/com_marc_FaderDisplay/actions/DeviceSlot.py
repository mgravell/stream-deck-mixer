from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from .ChannelSlot import ChannelSlot


class DeviceSlot(ChannelSlot):
    """
    One key of an output-device fader column on the Outputs page.

    Devices are every sink that is not a channel; the default output device is
    listed first and drawn with an accent ring (and takes the config's
    "master" color, keeping its own label). Same settings as ChannelSlot, slot 0..3.
    """

    def create_event_assigners(self):
        self.add_event_assigner(EventAssigner(
            id="device-slot",
            ui_label="Output Slot",
            default_events=[Input.Key.Events.DOWN],
            callback=self._on_press,
        ))

    def on_ready(self):
        self.plugin_base.add_device_slot(self)
        self.render()

    def on_removed_from_cache(self):
        self.plugin_base.remove_device_slot(self)

    def _channel(self):
        return self.plugin_base.get_device(int(self.get_settings().get("slot", 0)))

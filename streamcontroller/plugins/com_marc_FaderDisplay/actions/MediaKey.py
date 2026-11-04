import subprocess

from PIL import Image, ImageDraw

from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.ActionCore import ActionCore
from src.backend.PluginManager.EventAssigner import EventAssigner


class MediaKey(ActionCore):
    """
    A media-transport key driven through MPRIS (via host playerctl).

    Settings (set in the page JSON; no config UI):
      media-command: "play-pause" | "previous" | "next"
    """

    BG = (24, 24, 24, 255)
    GLYPH = (240, 240, 240, 255)
    IDLE = (110, 110, 110, 255)  # no media player available

    COMMANDS = ("play-pause", "previous", "next")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_configuration = False
        # the core does not call this itself; actions register their own assigners
        self.create_event_assigners()

    def on_ready(self):
        self.plugin_base.add_media_key(self)
        self.render()

    def on_update(self):
        self.render()

    def on_tick(self):
        self.render()

    def on_removed_from_cache(self):
        self.plugin_base.remove_media_key(self)

    def create_event_assigners(self):
        self.add_event_assigner(EventAssigner(
            id="media-key",
            ui_label="Media Key",
            default_events=[Input.Key.Events.DOWN],
            callback=self._on_press,
        ))

    def _on_press(self, event):
        command = self.get_settings().get("media-command")
        if command not in self.COMMANDS:
            return
        # cwd="/": flatpak-spawn --host replays the caller's cwd on the host,
        # and the sandbox's /app/... paths don't exist there
        subprocess.Popen(
            ["flatpak-spawn", "--host", "playerctl", command],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd="/",
        )

    def render(self):
        command = self.get_settings().get("media-command")
        if command not in self.COMMANDS:
            return

        status = self.plugin_base.media_status  # "Playing"/"Paused"/"Stopped"/None
        color = self.GLYPH if status else self.IDLE

        size = 96
        img = Image.new("RGBA", (size, size), self.BG)
        draw = ImageDraw.Draw(img)

        if command == "play-pause":
            if status == "Playing":
                # pause: two bars
                draw.rectangle([34, 30, 44, 66], fill=color)
                draw.rectangle([52, 30, 62, 66], fill=color)
            else:
                # play: right-pointing triangle
                draw.polygon([(38, 28), (38, 68), (72, 48)], fill=color)
        elif command == "previous":
            # bar + left-pointing triangle
            draw.rectangle([28, 30, 35, 66], fill=color)
            draw.polygon([(66, 30), (66, 66), (41, 48)], fill=color)
        elif command == "next":
            # right-pointing triangle + bar
            draw.polygon([(30, 30), (30, 66), (55, 48)], fill=color)
            draw.rectangle([61, 30, 68, 66], fill=color)

        self.set_media(image=img, size=1.0)
        self.set_top_label("")
        self.set_center_label("")
        self.set_bottom_label("")

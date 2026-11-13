import hashlib

from PIL import Image, ImageDraw

from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from .Segment import Segment


class AppSlot(Segment):
    """
    One key of a dynamic per-application fader column on the Apps page.

    The column shows the Nth audio-playing application (N = slot +
    the plugin's paging offset); empty slots render dark. Reuses Segment's
    drawing (fill, meter strip, glyphs) but sources its state from the
    plugin's app registry instead of a fixed sink.

    Settings (page JSON):
      slot:        column index 0..3
      glyph:       "up" | "down" | "mute" (also selects the press behavior)
      range-low/range-high, text, max-volume: as for Segment
    """

    # stable per-app colors, assigned by name hash
    PALETTE = [
        (0, 165, 185),    # teal
        (150, 90, 220),   # purple
        (225, 125, 0),    # orange
        (205, 60, 130),   # pink
        (95, 180, 60),    # lime
        (90, 130, 235),   # steel blue
        (220, 180, 0),    # gold
        (200, 80, 60),    # brick
    ]
    EMPTY_TRACK = (18, 18, 18, 255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.create_event_assigners()

    def create_event_assigners(self):
        self.add_event_assigner(EventAssigner(
            id="app-slot",
            ui_label="App Slot",
            default_events=[Input.Key.Events.DOWN],
            callback=self._on_press,
        ))

    def on_ready(self):
        self.plugin_base.add_app_slot(self)
        self.render()

    def on_removed_from_cache(self):
        self.plugin_base.remove_app_slot(self)

    def _app(self):
        return self.plugin_base.get_app(int(self.get_settings().get("slot", 0)))

    def _on_press(self, event):
        app = self._app()
        if app is None:
            return
        glyph = self.get_settings().get("glyph")
        if glyph == "up":
            self.plugin_base.adjust_app_volume(app, +10)
        elif glyph == "down":
            self.plugin_base.adjust_app_volume(app, -10)
        elif glyph == "mute":
            self.plugin_base.toggle_app_mute(app)

    @classmethod
    def app_color(cls, name: str):
        digest = hashlib.md5(name.encode("utf-8", "replace")).digest()
        return cls.PALETTE[digest[0] % len(cls.PALETTE)]

    def render(self):
        settings = self.get_settings()
        lo = float(settings.get("range-low", 0.0))
        hi = float(settings.get("range-high", 1.0))
        glyph = settings.get("glyph", "none")
        size = 96

        app = self._app()
        if app is None:
            img = Image.new("RGBA", (size, size), self.EMPTY_TRACK)
            self.set_media(image=img, size=1.0)
            self.set_top_label("")
            self.set_center_label("")
            self.set_bottom_label("")
            return

        volume, muted = app["volume"], app["muted"]
        color = self.app_color(app["name"])
        if muted:
            color = tuple(round(c * self.MUTED_BRIGHTNESS) for c in color)

        max_volume = float(settings.get("max-volume", 150))
        frac = max(0.0, min(1.0, volume / max_volume))
        fill = (frac - lo) / (hi - lo) if hi > lo else 0.0
        fill = max(0.0, min(1.0, fill))

        img = Image.new("RGBA", (size, size), self.TRACK)
        draw = ImageDraw.Draw(img)
        filled_px = round(size * fill)
        if filled_px > 0:
            draw.rectangle([0, size - filled_px, size, size], fill=color + (255,))
        if 0 < filled_px < size:
            draw.rectangle([0, size - filled_px - 2, size, size - filled_px], fill=self.CAP)

        self._draw_meter_bars(draw, size,
                              self.plugin_base.get_app_meter_display(app), lo, hi)
        self._draw_glyph(draw, glyph, muted)

        if glyph == "up":  # top key: app icon in the (otherwise empty) bottom half
            icon = self.plugin_base.get_app_icon(app)
            if icon is not None:
                img.paste(icon, (55 - icon.width // 2, 70 - icon.height // 2), icon)

        self.set_media(image=img, size=1.0)

        text = settings.get("text", "none")
        if text == "volume":
            self.set_bottom_label(str(volume))
        elif text == "label":
            name = app["name"]
            self.set_bottom_label(name if len(name) <= 9 else name[:8] + "…")
        else:
            self.set_bottom_label("")
        self.set_top_label("")
        self.set_center_label("")

    def meter_px(self):
        """Quantized meter pixels; the display loop re-renders on change."""
        app = self._app()
        if app is None:
            return None
        settings = self.get_settings()
        lo = float(settings.get("range-low", 0.0))
        hi = float(settings.get("range-high", 1.0))
        (bar_l, bar_r), (hold_l, hold_r) = self.plugin_base.get_app_meter_display(app)
        return tuple(self._col_to_px(f, lo, hi) // 2
                     for f in (bar_l, bar_r, hold_l, hold_r))

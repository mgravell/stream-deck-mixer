from PIL import Image, ImageDraw

from src.backend.PluginManager.ActionCore import ActionCore


class Segment(ActionCore):
    """
    One key's slice of a multi-key vertical fader.

    Settings (set in the page JSON; no config UI):
      pulse-name:  sink whose volume to display
      range-low:   fraction of the fader where this key starts (e.g. 0.3333)
      range-high:  fraction where it ends (e.g. 0.6667)
      color:       [r, g, b] fill color
      glyph:       "up" | "down" | "mute" | "none" - drawn in the top half
      text:        "none" | "volume" | "label" - bottom-half text content
      label:       text shown when text == "label" (e.g. "Media")
      max-volume:  volume (%) that fills the whole fader (default 100)
    """

    TRACK = (24, 24, 24, 255)
    MUTED_BRIGHTNESS = 0.35
    CAP = (235, 235, 235, 255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_configuration = False

    def on_ready(self):
        self.plugin_base.add_segment(self)
        self.render()

    def on_update(self):
        self.render()

    def on_tick(self):
        self.render()

    def on_removed_from_cache(self):
        self.plugin_base.remove_segment(self)

    def render(self):
        settings = self.get_settings()
        name = settings.get("pulse-name")
        if not name:
            return

        state = self.plugin_base.get_sink_state(name)
        if state is None:
            return
        volume, muted = state

        lo = float(settings.get("range-low", 0.0))
        hi = float(settings.get("range-high", 1.0))
        color = tuple(settings.get("color", [200, 200, 200]))[:3]
        if muted:
            color = tuple(round(c * self.MUTED_BRIGHTNESS) for c in color)

        max_volume = float(settings.get("max-volume", 100))
        frac = max(0.0, min(1.0, volume / max_volume))
        fill = (frac - lo) / (hi - lo) if hi > lo else 0.0
        fill = max(0.0, min(1.0, fill))

        size = 96
        img = Image.new("RGBA", (size, size), self.TRACK)
        draw = ImageDraw.Draw(img)
        filled_px = round(size * fill)
        if filled_px > 0:
            draw.rectangle([0, size - filled_px, size, size], fill=color + (255,))
        if 0 < filled_px < size:
            # fader cap: thin light line marking the level inside this segment
            draw.rectangle([0, size - filled_px - 2, size, size - filled_px], fill=self.CAP)

        self._draw_glyph(draw, settings.get("glyph", "none"), muted)
        self.set_media(image=img, size=1.0)

        text = settings.get("text", "none")
        if text == "volume":
            self.set_bottom_label(str(volume))
        elif text == "label":
            self.set_bottom_label(settings.get("label", ""))
        else:
            self.set_bottom_label("")
        self.set_top_label("")
        self.set_center_label("")

    GLYPH = (240, 240, 240, 255)
    MUTE_X = (255, 70, 60, 255)
    OUTLINE = (12, 12, 12, 255)

    @staticmethod
    def _stamp(shape_fn, outline):
        """Draw shape_fn with a dark outline so it reads on any fill color."""
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, -2), (-2, 2), (2, 2)):
            shape_fn(dx, dy, outline)
        shape_fn(0, 0, None)  # None -> caller uses its real color

    def _draw_glyph(self, draw: ImageDraw.ImageDraw, glyph: str, muted: bool):
        # glyphs live in the top half of the 96x96 canvas, centered on (48, 24)
        cx, cy = 48, 24
        if glyph == "up":
            def shape(dx, dy, col):
                draw.polygon([(cx - 15 + dx, cy + 8 + dy), (cx + 15 + dx, cy + 8 + dy),
                              (cx + dx, cy - 10 + dy)], fill=col or self.GLYPH)
            self._stamp(shape, self.OUTLINE)
        elif glyph == "down":
            def shape(dx, dy, col):
                draw.polygon([(cx - 15 + dx, cy - 8 + dy), (cx + 15 + dx, cy - 8 + dy),
                              (cx + dx, cy + 10 + dy)], fill=col or self.GLYPH)
            self._stamp(shape, self.OUTLINE)
        elif glyph == "mute":
            # speaker: box + cone pointing right; red when muted
            body = self.MUTE_X if muted else self.GLYPH

            def speaker(dx, dy, col):
                draw.polygon(
                    [(cx - 16 + dx, cy - 5 + dy), (cx - 8 + dx, cy - 5 + dy), (cx + 2 + dx, cy - 13 + dy),
                     (cx + 2 + dx, cy + 13 + dy), (cx - 8 + dx, cy + 5 + dy), (cx - 16 + dx, cy + 5 + dy)],
                    fill=col or body,
                )
            self._stamp(speaker, self.OUTLINE)

            if muted:
                def cross(dx, dy, col):
                    draw.line([(cx + 7 + dx, cy - 8 + dy), (cx + 18 + dx, cy + 8 + dy)],
                              fill=col or self.MUTE_X, width=4)
                    draw.line([(cx + 18 + dx, cy - 8 + dy), (cx + 7 + dx, cy + 8 + dy)],
                              fill=col or self.MUTE_X, width=4)
                self._stamp(cross, self.OUTLINE)
            else:
                def waves(dx, dy, col):
                    draw.arc([cx + 4 + dx, cy - 10 + dy, cx + 20 + dx, cy + 10 + dy],
                             -55, 55, fill=col or self.GLYPH, width=3)
                self._stamp(waves, self.OUTLINE)

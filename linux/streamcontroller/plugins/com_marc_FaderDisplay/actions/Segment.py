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
      meter-target: PipeWire node whose live level is drawn as a stereo
                   VU strip on the left edge (optional)
    """

    TRACK = (24, 24, 24, 255)
    MUTED_BRIGHTNESS = 0.35
    CAP = (235, 235, 235, 255)

    # VU meter strip (left edge): two 7px bars (L|R) with a 1px gap
    METER_W = 15
    METER_TRACK = (10, 10, 10, 255)
    METER_GREEN = (60, 205, 90, 255)
    METER_YELLOW = (235, 200, 60, 255)
    METER_RED = (240, 70, 55, 255)
    METER_HOLD = (235, 235, 235, 255)
    # zone boundaries as column fractions; must match _amp_to_frac in main.py
    ZONE_12DB = (42 - 12) / 42 * (2 / 3)  # -12 dBFS
    ZONE_0DB = 2 / 3                      # 0 dBFS (= the 100%-volume line)

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

        if settings.get("meter-target"):
            self._draw_meter(draw, size, settings.get("meter-target"), lo, hi)

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

    def _col_to_px(self, frac: float, lo: float, hi: float, size: int = 96) -> int:
        """Column fraction -> pixels above this key's bottom edge, clamped."""
        if hi <= lo:
            return 0
        rel = (frac - lo) / (hi - lo)
        return round(size * max(0.0, min(1.0, rel)))

    def _draw_meter(self, draw: ImageDraw.ImageDraw, size: int, target: str,
                    lo: float, hi: float):
        self._draw_meter_bars(draw, size, self.plugin_base.get_meter_display(target), lo, hi)

    def _draw_meter_bars(self, draw: ImageDraw.ImageDraw, size: int, data: tuple,
                         lo: float, hi: float):
        (bar_l, bar_r), (hold_l, hold_r) = data
        half = (self.METER_W - 1) // 2  # 7px per side
        draw.rectangle([0, 0, self.METER_W - 1, size], fill=self.METER_TRACK)
        for x0, x1, bar, hold in ((0, half - 1, bar_l, hold_l),
                                  (self.METER_W - half, self.METER_W - 1, bar_r, hold_r)):
            for z0, z1, color in ((0.0, self.ZONE_12DB, self.METER_GREEN),
                                  (self.ZONE_12DB, self.ZONE_0DB, self.METER_YELLOW),
                                  (self.ZONE_0DB, 1.0, self.METER_RED)):
                top = self._col_to_px(min(bar, z1), lo, hi, size)
                bottom = self._col_to_px(z0, lo, hi, size)
                if top > bottom:
                    draw.rectangle([x0, size - top, x1, size - bottom], fill=color)
            if hold > 0 and hi > lo and lo < hold <= hi:
                y = size - self._col_to_px(hold, lo, hi, size)
                draw.rectangle([x0, max(0, y - 2), x1, min(size, y)], fill=self.METER_HOLD)

    def meter_px(self):
        """Quantized meter pixels for this key; the display loop re-renders on change."""
        settings = self.get_settings()
        target = settings.get("meter-target")
        if not target:
            return None
        lo = float(settings.get("range-low", 0.0))
        hi = float(settings.get("range-high", 1.0))
        (bar_l, bar_r), (hold_l, hold_r) = self.plugin_base.get_meter_display(target)
        return tuple(self._col_to_px(f, lo, hi) // 2
                     for f in (bar_l, bar_r, hold_l, hold_r))

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
        elif glyph == "mic":
            # microphone: capsule + cradle; red with a slash when muted
            body = self.MUTE_X if muted else self.GLYPH

            def capsule(dx, dy, col):
                draw.rounded_rectangle([cx - 7 + dx, cy - 15 + dy, cx + 7 + dx, cy + 1 + dy],
                                       radius=7, fill=col or body)
            self._stamp(capsule, self.OUTLINE)

            def cradle(dx, dy, col):
                draw.arc([cx - 12 + dx, cy - 8 + dy, cx + 12 + dx, cy + 8 + dy],
                         0, 180, fill=col or self.GLYPH, width=3)
                draw.line([(cx + dx, cy + 8 + dy), (cx + dx, cy + 14 + dy)],
                          fill=col or self.GLYPH, width=3)
                draw.line([(cx - 7 + dx, cy + 14 + dy), (cx + 7 + dx, cy + 14 + dy)],
                          fill=col or self.GLYPH, width=3)
            self._stamp(cradle, self.OUTLINE)

            if muted:
                def slash(dx, dy, col):
                    draw.line([(cx - 14 + dx, cy + 13 + dy), (cx + 14 + dx, cy - 13 + dy)],
                              fill=col or self.MUTE_X, width=4)
                self._stamp(slash, self.OUTLINE)
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

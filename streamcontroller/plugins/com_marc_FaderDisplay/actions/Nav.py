import os

from PIL import Image, ImageDraw
from loguru import logger as log

from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.ActionCore import ActionCore
from src.backend.PluginManager.EventAssigner import EventAssigner

import globals as gl


class _NavBase(ActionCore):
    BG = (24, 24, 24, 255)
    GLYPH = (240, 240, 240, 255)
    DIM = (110, 110, 110, 255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_configuration = False
        self.create_event_assigners()

    def on_ready(self):
        self.render()

    def on_update(self):
        self.render()

    def on_tick(self):
        self.render()


class PageSwitch(_NavBase):
    """
    Jump to another page. Settings:
      page:  page name without extension, e.g. "Apps"
      style: "apps" (2x2 grid glyph) | "mixer" (fader-bars glyph)
    """

    def create_event_assigners(self):
        self.add_event_assigner(EventAssigner(
            id="page-switch",
            ui_label="Switch Page",
            default_events=[Input.Key.Events.DOWN],
            callback=self._on_press,
        ))

    def _on_press(self, event):
        name = self.get_settings().get("page")
        if not name:
            return
        try:
            path = os.path.join(gl.DATA_PATH, "pages", f"{name}.json")
            page = gl.page_manager.get_page(path, self.deck_controller)
            self.deck_controller.load_page(page)
        except Exception as e:
            log.error(f"FaderDisplay: page switch to {name} failed: {e}")

    def render(self):
        settings = self.get_settings()
        size = 96
        img = Image.new("RGBA", (size, size), self.BG)
        draw = ImageDraw.Draw(img)
        cx, cy = 48, 38
        if settings.get("style") == "mixer":
            # three mini faders with caps at different heights
            for dx, cap in ((-16, 14), (0, -8), (16, 6)):
                draw.rectangle([cx + dx - 2, cy - 20, cx + dx + 2, cy + 20], fill=self.DIM)
                draw.rectangle([cx + dx - 8, cy - cap - 3, cx + dx + 8, cy - cap + 3],
                               fill=self.GLYPH)
        else:
            # 2x2 app grid
            for dx in (-18, 4):
                for dy in (-18, 4):
                    draw.rounded_rectangle([cx + dx, cy + dy, cx + dx + 14, cy + dy + 14],
                                           4, fill=self.GLYPH)
        self.set_media(image=img, size=1.0)
        self.set_top_label("")
        self.set_center_label("")
        self.set_bottom_label(self.get_settings().get("page", ""))


class AppPager(_NavBase):
    """Advance the Apps page to the next group of four applications."""

    PER_PAGE = 4
    EMPTY_LABEL = "no audio"

    @staticmethod
    def _unit(total: int) -> str:
        return "app" if total == 1 else "apps"

    def create_event_assigners(self):
        self.add_event_assigner(EventAssigner(
            id="app-pager",
            ui_label="Next Apps",
            default_events=[Input.Key.Events.DOWN],
            callback=self._on_press,
        ))

    def on_ready(self):
        self.plugin_base.add_pager(self)
        self.render()

    def on_removed_from_cache(self):
        self.plugin_base.remove_pager(self)

    def _counts(self):
        return self.plugin_base.app_count(), self.plugin_base.app_offset

    def _on_press(self, event):
        self.plugin_base.advance_app_page()

    def render(self):
        total, offset = self._counts()
        color = self.GLYPH if total > self.PER_PAGE else self.DIM

        size = 96
        img = Image.new("RGBA", (size, size), self.BG)
        draw = ImageDraw.Draw(img)
        cx, cy = 48, 38
        for dx in (-10, 8):  # double chevron right
            draw.polygon([(cx + dx - 6, cy - 14), (cx + dx + 6, cy),
                          (cx + dx - 6, cy + 14)], fill=color)
        self.set_media(image=img, size=1.0)
        self.set_top_label("")
        self.set_center_label("")
        if total > self.PER_PAGE:
            self.set_bottom_label(f"{offset + 1}-{min(offset + self.PER_PAGE, total)}/{total}")
        elif total:
            self.set_bottom_label(f"{total} {self._unit(total)}")
        else:
            self.set_bottom_label(self.EMPTY_LABEL)


class ChannelPager(AppPager):
    """Advance the Mixer page to the next group of three channels."""

    PER_PAGE = 3
    EMPTY_LABEL = "no channels"

    @staticmethod
    def _unit(total: int) -> str:
        return "ch"

    def create_event_assigners(self):
        self.add_event_assigner(EventAssigner(
            id="channel-pager",
            ui_label="Next Channels",
            default_events=[Input.Key.Events.DOWN],
            callback=self._on_press,
        ))

    def on_ready(self):
        self.plugin_base.add_channel_pager(self)
        self.render()

    def on_removed_from_cache(self):
        self.plugin_base.remove_channel_pager(self)

    def _counts(self):
        return self.plugin_base.channel_count(), self.plugin_base.channel_offset

    def _on_press(self, event):
        self.plugin_base.advance_channel_page()

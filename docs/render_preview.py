#!/usr/bin/env python3
"""
Generate docs/deck-channels.png, deck-outputs.png, deck-apps.png and
deck-inputs.png: faithful mock-ups of the four pages as they appear on the
Stream Deck, by running the real FaderDisplay rendering code against a staged
scene.

Needs PIL + cairosvg, which the StreamController flatpak ships:

    flatpak run --command=python3 com.core447.StreamController docs/render_preview.py
"""

import importlib.util
import io
import json
import os
import sys
import types
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "streamcontroller", "plugins", "com_marc_FaderDisplay")
PAGES = os.path.join(ROOT, "streamcontroller", "pages")

# ---- stub the StreamController framework the actions import --------------


def _stub(name, **attrs):
    mod = sys.modules.get(name) or types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod


_stub("src")
_stub("src.backend")
_stub("src.backend.PluginManager")
_stub("src.backend.PluginManager.ActionCore", ActionCore=object)
_stub("src.backend.PluginManager.EventAssigner", EventAssigner=object)
_stub("src.backend.DeckManagement")
_stub("src.backend.DeckManagement.InputIdentifier",
      Input=SimpleNamespace(Key=SimpleNamespace(Events=SimpleNamespace(DOWN=None))))
_stub("globals", DATA_PATH="", page_manager=None)

package = types.ModuleType("fdp_actions")
package.__path__ = [os.path.join(PLUGIN, "actions")]
sys.modules["fdp_actions"] = package


def _load(modname):
    spec = importlib.util.spec_from_file_location(
        f"fdp_actions.{modname}", os.path.join(PLUGIN, "actions", f"{modname}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"fdp_actions.{modname}"] = mod
    spec.loader.exec_module(mod)
    return mod


Segment = _load("Segment").Segment
MediaKey = _load("MediaKey").MediaKey
AppSlot = _load("AppSlot").AppSlot
ChannelSlot = _load("ChannelSlot").ChannelSlot
SourceSlot = _load("SourceSlot").SourceSlot
DeviceSlot = _load("DeviceSlot").DeviceSlot
_nav = _load("Nav")
PageSwitch, AppPager = _nav.PageSwitch, _nav.AppPager
ChannelPager, SourcePager, DevicePager = _nav.ChannelPager, _nav.SourcePager, _nav.DevicePager

# ---- staged scene --------------------------------------------------------

G3 = ("alsa_output.usb-Creative_Technology_Ltd_Sound_Blaster_G3_"
      "CC08F2F2C2F3E386-03.analog-stereo")

SINKS = {  # name -> (volume %, muted)
    "media_channel": (80, False),
    "chat_channel": (100, True),
    "game_channel": (65, False),
    G3: (100, False),
}

METERS = {  # meter-target -> ((bar L, bar R), (hold L, hold R)) column fractions
    "media_channel.out": ((0.58, 0.49), (0.66, 0.57)),
    "chat_channel.out": ((0.0, 0.0), (0.0, 0.0)),
    "game_channel.out": ((0.33, 0.29), (0.42, 0.37)),
    G3 + ".monitor": ((0.60, 0.54), (0.70, 0.62)),
}

APPS = [  # the Apps page scene: Chrome loud, Heroic quiet, LM Studio muted
    {"name": "Google Chrome", "volume": 90, "muted": False, "inputs": [1],
     "icon_candidates": [], "_meter": ((0.56, 0.50), (0.63, 0.58))},
    {"name": "Heroic", "volume": 70, "muted": False, "inputs": [2],
     "icon_candidates": [], "_meter": ((0.30, 0.27), (0.38, 0.34))},
    {"name": "LM Studio", "volume": 100, "muted": True, "inputs": [3],
     "icon_candidates": [], "_meter": ((0.0, 0.0), (0.0, 0.0))},
]

HOME = os.path.expanduser("~")
FLATPAK_ICONS = f"{HOME}/.local/share/flatpak/exports/share/icons/hicolor"
ICON_FILES = {
    "Google Chrome": f"{FLATPAK_ICONS}/scalable/apps/com.google.Chrome.svg",
    "Heroic": f"{FLATPAK_ICONS}/128x128/apps/com.heroicgameslauncher.hgl.png",
    "LM Studio": f"{FLATPAK_ICONS}/128x128/apps/ai.lmstudio.lm-studio.png",
}


def load_icon(path):
    # flatpak masks ~/.local/share/flatpak from sandboxes; read via the host
    # (exactly as the plugin's own icon resolver does)
    try:
        import subprocess
        data = subprocess.run(["flatpak-spawn", "--host", "cat", path],
                              capture_output=True, timeout=15, cwd="/").stdout
        if not data:
            return None
        if path.endswith(".svg"):
            import cairosvg
            data = cairosvg.svg2png(bytestring=data, output_width=40, output_height=40)
        icon = Image.open(io.BytesIO(data)).convert("RGBA")
        icon.thumbnail((38, 38), Image.Resampling.LANCZOS)
        return icon
    except Exception:
        return None


ICONS = {name: load_icon(path) for name, path in ICON_FILES.items()}

CHANNELS = [  # mirrors the example fader-display.json config
    {"name": "media_channel", "label": "Media", "color": (205, 50, 45),
     "meter_target": "media_channel.out"},
    {"name": "chat_channel", "label": "Chat", "color": (45, 175, 85),
     "meter_target": "chat_channel.out"},
    {"name": "game_channel", "label": "Game", "color": (55, 115, 235),
     "meter_target": "game_channel.out"},
]
MASTER = {"name": G3, "label": "G3", "color": (235, 145, 35),
          "meter_target": G3 + ".monitor", "accent": True}

HDMI, SPDIF, PHONES = "hdmi", "spdif", "phones"
DEVICES = [  # Outputs page scene: G3 is default (accented, metered, "Master")
    MASTER,
    {"name": HDMI, "label": "HDMI", "color": (90, 130, 235), "meter_target": None},
    {"name": SPDIF, "label": "SPDIF", "color": (150, 90, 220), "meter_target": None},
    {"name": PHONES, "label": "Phones", "color": (0, 165, 185), "meter_target": None},
]

G3_MIC = "g3_mic"
SOURCES = [  # Input page scene: G3 mic hot, USB mic muted, line-in quiet
    {"name": G3_MIC, "label": "G3 Mic", "color": (0, 165, 185), "meter_target": G3_MIC},
    {"name": "usb_mic", "label": "USB Mic", "color": (150, 90, 220), "meter_target": "usb_mic"},
    {"name": "line_in", "label": "Line In", "color": (95, 180, 60), "meter_target": "line_in"},
]
SOURCES[0]["accent"] = True  # default input device
SOURCE_STATE = {G3_MIC: (100, False), "usb_mic": (100, True), "line_in": (80, False)}
SINKS.update({HDMI: (100, False), SPDIF: (100, False), PHONES: (70, True)})
METERS[G3_MIC] = ((0.52, 0.47), (0.60, 0.55))
METERS["line_in"] = ((0.22, 0.20), (0.30, 0.27))

plugin_base = SimpleNamespace(
    get_sink_state=SINKS.get,
    get_meter_display=lambda t: METERS.get(t, ((0.0, 0.0), (0.0, 0.0))),
    media_status="Playing",
    apps=APPS,
    app_offset=0,
    app_count=lambda: len(APPS),
    get_app=lambda slot: APPS[slot] if 0 <= slot < len(APPS) else None,
    get_app_meter_display=lambda app: app["_meter"],
    get_app_icon=lambda app: ICONS.get(app["name"]),
    get_channel=lambda slot: CHANNELS[slot] if 0 <= slot < len(CHANNELS) else None,
    get_master_channel=lambda: MASTER,
    channel_count=lambda: len(CHANNELS),
    channel_offset=0,
    get_source=lambda slot: SOURCES[slot] if 0 <= slot < len(SOURCES) else None,
    get_source_state=SOURCE_STATE.get,
    source_count=lambda: len(SOURCES),
    source_offset=0,
    get_device=lambda slot: DEVICES[slot] if 0 <= slot < len(DEVICES) else None,
    device_count=lambda: 5,  # one more than shown, so the pager reads 1-4/5
    device_offset=0,
)

# ---- render every key through the real action code -----------------------

ACTIONS = {"Segment": Segment, "MediaKey": MediaKey, "AppSlot": AppSlot,
           "ChannelSlot": ChannelSlot, "SourceSlot": SourceSlot,
           "DeviceSlot": DeviceSlot, "PageSwitch": PageSwitch,
           "AppPager": AppPager, "ChannelPager": ChannelPager,
           "SourcePager": SourcePager, "DevicePager": DevicePager}


def render_key(cls, settings):
    inst = cls.__new__(cls)  # bypass ActionCore __init__
    inst.plugin_base = plugin_base
    captured = {}
    inst.get_settings = lambda: settings
    inst.set_media = lambda image=None, size=1.0: captured.update(img=image)
    inst.set_bottom_label = lambda s: captured.update(label=s)
    inst.set_top_label = lambda s: None
    inst.set_center_label = lambda s: None
    inst.render()
    return captured.get("img"), captured.get("label")


def find_font(size):
    for path in ("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/run/host/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


FONT = find_font(16)


def render_page(page_file, out_file):
    page = json.load(open(os.path.join(PAGES, page_file)))
    keys = {}
    for coord, key in page["keys"].items():
        x, y = map(int, coord.split("x"))
        img = label = None
        for action in key["states"]["0"]["actions"]:
            suffix = action["id"].split("::")[-1]
            if suffix in ACTIONS:
                img, label = render_key(ACTIONS[suffix], action["settings"])
        if img is None:
            img = Image.new("RGBA", (96, 96), (18, 18, 18, 255))  # unassigned key
        if label:  # StreamController composites labels over the image; mimic that
            ImageDraw.Draw(img).text((48, 93), str(label), anchor="ms", font=FONT,
                                     fill=(255, 255, 255), stroke_width=2,
                                     stroke_fill=(0, 0, 0))
        keys[(x, y)] = img

    KEY, GAP, MARGIN, RADIUS = 192, 26, 56, 26
    width = MARGIN * 2 + 5 * KEY + 4 * GAP
    height = MARGIN * 2 + 3 * KEY + 2 * GAP
    canvas = Image.new("RGB", (width, height), (17, 17, 19))
    mask = Image.new("L", (KEY, KEY), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, KEY - 1, KEY - 1], RADIUS, fill=255)
    for x in range(5):
        for y in range(3):
            img = keys.get((x, y), Image.new("RGBA", (96, 96), (18, 18, 18, 255)))
            up = img.convert("RGB").resize((KEY, KEY), Image.Resampling.LANCZOS)
            canvas.paste(up, (MARGIN + x * (KEY + GAP), MARGIN + y * (KEY + GAP)), mask)
    out = os.path.join(ROOT, "docs", out_file)
    canvas.save(out)
    print(f"wrote {out}")


render_page("Channels.json", "deck-channels.png")
render_page("Outputs.json", "deck-outputs.png")
render_page("Apps.json", "deck-apps.png")
render_page("Inputs.json", "deck-inputs.png")

#!/usr/bin/env python3
"""
Generate docs/deck-preview.png: a faithful mock-up of the Mixer page as it
appears on the Stream Deck, by running the real FaderDisplay rendering code
(Segment + MediaKey) against a staged scene.

Needs PIL, which the StreamController flatpak ships:

    flatpak run --command=python3 com.core447.StreamController docs/render_preview.py

Scene: music playing on Media (meters bouncing), Chat muted, Game quiet,
Master showing the final mix, media transport mid-playback.
"""

import importlib.util
import json
import os
import sys
import types
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "streamcontroller", "plugins", "com_marc_FaderDisplay")
OUT = os.path.join(ROOT, "docs", "deck-preview.png")

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


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


Segment = _load(os.path.join(PLUGIN, "actions", "Segment.py"), "_seg").Segment
MediaKey = _load(os.path.join(PLUGIN, "actions", "MediaKey.py"), "_mk").MediaKey

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

plugin_base = SimpleNamespace(
    get_sink_state=SINKS.get,
    get_meter_display=lambda t: METERS.get(t, ((0.0, 0.0), (0.0, 0.0))),
    media_status="Playing",
)

# ---- render every key through the real action code -----------------------


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


page = json.load(open(os.path.join(ROOT, "streamcontroller", "pages", "Mixer.json")))
font = find_font(16)
keys = {}
for coord, key in page["keys"].items():
    x, y = map(int, coord.split("x"))
    img = label = None
    for action in key["states"]["0"]["actions"]:
        if action["id"].endswith("::Segment"):
            img, label = render_key(Segment, action["settings"])
        elif action["id"].endswith("::MediaKey"):
            img, label = render_key(MediaKey, action["settings"])
    if img is None:
        continue
    if label:  # StreamController composites labels over the image; mimic that
        draw = ImageDraw.Draw(img)
        draw.text((48, 93), str(label), anchor="ms", font=font,
                  fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))
    keys[(x, y)] = img

# ---- compose the deck face ------------------------------------------------

KEY, GAP, MARGIN, RADIUS = 192, 26, 56, 26
W = MARGIN * 2 + 5 * KEY + 4 * GAP
H = MARGIN * 2 + 3 * KEY + 2 * GAP
canvas = Image.new("RGB", (W, H), (17, 17, 19))

mask = Image.new("L", (KEY, KEY), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, KEY - 1, KEY - 1], RADIUS, fill=255)

for (x, y), img in keys.items():
    up = img.convert("RGB").resize((KEY, KEY), Image.Resampling.LANCZOS)
    pos = (MARGIN + x * (KEY + GAP), MARGIN + y * (KEY + GAP))
    canvas.paste(up, pos, mask)

canvas.save(OUT)
print(f"wrote {OUT} ({W}x{H}, {len(keys)} keys)")

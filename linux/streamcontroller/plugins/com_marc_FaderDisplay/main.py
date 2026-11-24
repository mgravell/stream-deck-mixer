"""
FaderDisplay: renders PipeWire/PulseAudio sink volumes as vertical faders
spread across columns of Stream Deck keys.

The plugin keeps one background thread listening for PulseAudio sink events;
whenever a watched sink's volume or mute state changes, every registered
Segment action re-renders its slice of the fader.
"""

import json
import math
import os
import subprocess
import threading
import time
from array import array

import pulsectl
from loguru import logger as log

import globals as gl

from src.backend.PluginManager.PluginBase import PluginBase
from src.backend.PluginManager.ActionHolder import ActionHolder
from src.backend.PluginManager.ActionInputSupport import ActionInputSupport
from src.backend.DeckManagement.InputIdentifier import Input

from .actions.Segment import Segment
from .actions.MediaKey import MediaKey
from .actions.AppSlot import AppSlot
from .actions.ChannelSlot import ChannelSlot
from .actions.SourceSlot import SourceSlot
from .actions.DeviceSlot import DeviceSlot
from .actions.Nav import PageSwitch, AppPager, ChannelPager, SourcePager, DevicePager


def _amp_to_frac(amp: float) -> float:
    """
    Map a peak amplitude to a fader-column fraction (0..1).
    0 dBFS lands on the 100%-volume line (2/3 of the column, matching
    max-volume=150), with -42 dB at the bottom and +6 dB of overdrive
    headroom in the top third.
    """
    if amp < 1e-4:
        return 0.0
    db = 20 * math.log10(amp)
    if db <= 0:
        return max(0.0, (db + 42) / 42) * (2 / 3)
    return 2 / 3 + min(db / 6, 1.0) / 3


class _MeterState:
    __slots__ = ("raw_max", "last_data", "level", "hold", "hold_ts", "proc", "wanted")

    def __init__(self):
        # all level fields are [left, right] amplitudes
        self.raw_max = [0.0, 0.0]  # peaks since the display loop last consumed them
        self.last_data = 0.0
        self.level = [0.0, 0.0]    # displayed bar level (post-ballistics)
        self.hold = [0.0, 0.0]     # peak-hold markers
        self.hold_ts = [0.0, 0.0]
        self.proc = None
        self.wanted = True


class FaderDisplay(PluginBase):
    def __init__(self):
        super().__init__()

        self._segments = []
        self._media_keys = []
        self._lock = threading.Lock()
        self.sink_state: dict[str, tuple[int, bool]] = {}  # name -> (volume %, muted)
        self.media_status: str | None = None  # "Playing"/"Paused"/"Stopped", None = no player

        self._meters: dict[str, _MeterState] = {}  # meter-target -> state
        # target -> ((bar_l, bar_r), (hold_l, hold_r)) as column fractions, for Segment.render
        self.meter_display: dict[str, tuple] = {}
        self._meter_display_started = False

        # discovered-channel registry (Channels page: virtual streams only)
        self.channels: list[dict] = []   # ordered; {"name", "label", "color", "meter_target"}
        self.channel_offset = 0
        self._channel_slots: list = []
        self._channel_pagers: list = []
        self._channel_meter_targets: set[str] = set()
        self._config: dict = {}
        self._config_mtime: float | None = -1.0

        # discovered output-device registry (Outputs page): every sink that is
        # not a channel; the default device comes first and is accented
        self.sink_devices: list[dict] = []
        self.device_offset = 0
        self._device_slots: list = []
        self._device_pagers: list = []
        self._device_meter_targets: set[str] = set()
        self.default_sink: str | None = None
        self.default_source: str | None = None

        # discovered-input registry (Inputs page): non-monitor sources
        self.source_state: dict[str, tuple[int, bool]] = {}  # name -> (volume %, muted)
        self.sources: list[dict] = []    # ordered; {"name", "label", "color", "meter_target"}
        self.source_offset = 0
        self._source_slots: list = []
        self._source_pagers: list = []
        self._source_meter_targets: set[str] = set()

        # per-application registry (Apps page)
        self.apps: list[dict] = []     # ordered; {"name", "icon_name", "volume", "muted", "inputs"}
        self.app_offset = 0            # paging offset into self.apps
        self._app_order: dict[str, int] = {}  # name -> first-seen rank (stable columns)
        self._app_meter_ids: set[str] = set()
        self._app_slots: list = []
        self._pagers: list = []
        self._icon_cache: dict[str, object] = {}  # icon_name -> PIL image or None
        self._icon_pending: set[str] = set()

        self.segment_holder = ActionHolder(
            plugin_base=self,
            action_base=Segment,
            action_id_suffix="Segment",
            action_name="Fader Segment",
            action_support={
                Input.Key: ActionInputSupport.SUPPORTED,
                Input.Dial: ActionInputSupport.UNSUPPORTED,
                Input.Touchscreen: ActionInputSupport.UNSUPPORTED,
            },
        )
        self.add_action_holder(self.segment_holder)

        for action_base, suffix, name in ((AppSlot, "AppSlot", "App Slot"),
                                          (ChannelSlot, "ChannelSlot", "Channel Slot"),
                                          (SourceSlot, "SourceSlot", "Input Slot"),
                                          (DeviceSlot, "DeviceSlot", "Output Slot"),
                                          (PageSwitch, "PageSwitch", "Switch Page"),
                                          (AppPager, "AppPager", "Next Apps"),
                                          (ChannelPager, "ChannelPager", "Next Channels"),
                                          (SourcePager, "SourcePager", "Next Inputs"),
                                          (DevicePager, "DevicePager", "Next Outputs")):
            self.add_action_holder(ActionHolder(
                plugin_base=self,
                action_base=action_base,
                action_id_suffix=suffix,
                action_name=name,
                action_support={
                    Input.Key: ActionInputSupport.SUPPORTED,
                    Input.Dial: ActionInputSupport.UNSUPPORTED,
                    Input.Touchscreen: ActionInputSupport.UNSUPPORTED,
                },
            ))

        self.media_key_holder = ActionHolder(
            plugin_base=self,
            action_base=MediaKey,
            action_id_suffix="MediaKey",
            action_name="Media Key",
            action_support={
                Input.Key: ActionInputSupport.SUPPORTED,
                Input.Dial: ActionInputSupport.UNSUPPORTED,
                Input.Touchscreen: ActionInputSupport.UNSUPPORTED,
            },
        )
        self.add_action_holder(self.media_key_holder)

        self.register()

        threading.Thread(target=self._monitor, daemon=True, name="fader-display-pulse").start()
        threading.Thread(target=self._media_monitor, daemon=True, name="fader-display-media").start()

    # Segment registry

    def add_segment(self, segment) -> None:
        with self._lock:
            if segment not in self._segments:
                self._segments.append(segment)
        self._sync_meters()

    def remove_segment(self, segment) -> None:
        with self._lock:
            if segment in self._segments:
                self._segments.remove(segment)
        self._sync_meters()

    def get_sink_state(self, name: str):
        return self.sink_state.get(name)

    def add_media_key(self, key) -> None:
        with self._lock:
            if key not in self._media_keys:
                self._media_keys.append(key)

    def remove_media_key(self, key) -> None:
        with self._lock:
            if key in self._media_keys:
                self._media_keys.remove(key)

    def add_app_slot(self, slot) -> None:
        with self._lock:
            if slot not in self._app_slots:
                self._app_slots.append(slot)

    def remove_app_slot(self, slot) -> None:
        with self._lock:
            if slot in self._app_slots:
                self._app_slots.remove(slot)

    def add_pager(self, pager) -> None:
        with self._lock:
            if pager not in self._pagers:
                self._pagers.append(pager)

    def remove_pager(self, pager) -> None:
        with self._lock:
            if pager in self._pagers:
                self._pagers.remove(pager)

    def add_channel_slot(self, slot) -> None:
        with self._lock:
            if slot not in self._channel_slots:
                self._channel_slots.append(slot)

    def remove_channel_slot(self, slot) -> None:
        with self._lock:
            if slot in self._channel_slots:
                self._channel_slots.remove(slot)

    def add_channel_pager(self, pager) -> None:
        with self._lock:
            if pager not in self._channel_pagers:
                self._channel_pagers.append(pager)

    def remove_channel_pager(self, pager) -> None:
        with self._lock:
            if pager in self._channel_pagers:
                self._channel_pagers.remove(pager)

    # Application registry (Apps page)

    def get_app(self, slot: int):
        index = self.app_offset + slot
        return self.apps[index] if 0 <= index < len(self.apps) else None

    def app_count(self) -> int:
        return len(self.apps)

    def advance_app_page(self) -> None:
        if len(self.apps) > 4:
            self.app_offset += 4
            if self.app_offset >= len(self.apps):
                self.app_offset = 0
        else:
            self.app_offset = 0
        self._notify_apps()

    def _notify_apps(self) -> None:
        with self._lock:
            targets = list(self._app_slots) + list(self._pagers)
        for target in targets:
            try:
                target.render()
            except Exception as e:
                log.error(f"FaderDisplay: app key render failed: {e}")

    def adjust_app_volume(self, app: dict, delta: int) -> None:
        target = max(0, min(150, app["volume"] + delta)) / 100
        try:
            with pulsectl.Pulse("fader-display-cmd") as pulse:
                for si in pulse.sink_input_list():
                    if si.index in app["inputs"]:
                        pulse.volume_set_all_chans(si, target)
        except Exception as e:
            log.error(f"FaderDisplay: app volume change failed: {e}")

    def toggle_app_mute(self, app: dict) -> None:
        target = not app["muted"]
        try:
            with pulsectl.Pulse("fader-display-cmd") as pulse:
                for index in app["inputs"]:
                    try:
                        pulse.sink_input_mute(index, target)
                    except Exception:
                        pass  # stream may have just vanished
        except Exception as e:
            log.error(f"FaderDisplay: app mute toggle failed: {e}")

    def get_app_meter_display(self, app: dict) -> tuple:
        """Combine the meter displays of all of an app's streams (max per channel)."""
        bars = [0.0, 0.0]
        holds = [0.0, 0.0]
        for index in app["inputs"]:
            (bl, br), (hl, hr) = self.meter_display.get(str(index), ((0.0, 0.0), (0.0, 0.0)))
            bars[0] = max(bars[0], bl)
            bars[1] = max(bars[1], br)
            holds[0] = max(holds[0], hl)
            holds[1] = max(holds[1], hr)
        return tuple(bars), tuple(holds)

    # Application icons: resolved from the host's icon themes in a background
    # thread (the sandbox can't see /usr/share/icons), cached by icon name.

    ICON_DIRS = ("/usr/share/icons", "/usr/share/pixmaps",
                 "/var/lib/flatpak/exports/share/icons",
                 "$HOME/.local/share/icons",
                 "$HOME/.local/share/flatpak/exports/share/icons")

    def get_app_icon(self, app: dict):
        key = app["name"]
        if key in self._icon_cache:
            return self._icon_cache[key]
        if key not in self._icon_pending:
            self._icon_pending.add(key)
            threading.Thread(target=self._resolve_icon,
                             args=(key, list(app["icon_candidates"])),
                             daemon=True, name=f"icon-{key[:20]}").start()
        return None

    def _resolve_icon(self, key: str, candidates: list) -> None:
        """Find an icon file on the host for any of the candidate names, with a
        fuzzy fallback on the app name (flatpak icons use reverse-DNS ids that
        rarely match the advertised icon name)."""
        icon = None
        try:
            candidates = [c for c in candidates
                          if c and all(ch.isalnum() or ch in "._-" for ch in c)]
            clauses = [f'-iname "{c}.png" -o -iname "{c}.svg"' for c in candidates]
            tokens = [t for t in "".join(ch if ch.isalnum() else " " for ch in key).split()
                      if len(t) >= 4]
            if tokens:
                fuzzy = max(tokens, key=len).lower()
                clauses.append(f'-iname "*{fuzzy}*.png" -o -iname "*{fuzzy}*.svg"')
            if clauses:
                script = ("find " + " ".join(f'"{d}"' for d in self.ICON_DIRS) +
                          " \\( " + " -o ".join(clauses) + " \\) 2>/dev/null | head -80")
                result = subprocess.run(["flatpak-spawn", "--host", "sh", "-c", script],
                                        capture_output=True, text=True, timeout=15, cwd="/")
                best = self._pick_icon_file(result.stdout.splitlines(), candidates)
                if best:
                    data = subprocess.run(["flatpak-spawn", "--host", "cat", best],
                                          capture_output=True, timeout=15, cwd="/").stdout
                    icon = self._load_icon(data, best.endswith(".svg"))
        except Exception as e:
            log.warning(f"FaderDisplay: icon lookup for {key} failed: {e}")
        self._icon_cache[key] = icon
        self._icon_pending.discard(key)
        if icon is not None:
            self._notify_apps()

    @staticmethod
    def _pick_icon_file(paths: list, candidates: list) -> str | None:
        stems = [c.lower() for c in candidates]

        def score(path: str) -> int:
            base = os.path.basename(path).rsplit(".", 1)[0].lower()
            points = 0
            if base in stems:  # exact candidate match beats any fuzzy hit
                points += 1000 - 10 * stems.index(base)
            png = path.endswith(".png")
            for size, size_points in (("/64x64/", 100), ("/96x96/", 90), ("/128x128/", 85),
                                      ("/48x48/", 80), ("/256x256/", 70), ("/32x32/", 50)):
                if size in path:
                    return points + (size_points if png else 20)
            if "/scalable/" in path:
                return points + 60
            return points + (40 if png else 30)
        paths = [p for p in paths if p.strip()]
        return max(paths, key=score) if paths else None

    @staticmethod
    def _load_icon(data: bytes, is_svg: bool):
        import io

        from PIL import Image
        if is_svg:
            import cairosvg
            data = cairosvg.svg2png(bytestring=data, output_width=40, output_height=40)
        icon = Image.open(io.BytesIO(data)).convert("RGBA")
        icon.thumbnail((38, 38), Image.Resampling.LANCZOS)
        return icon

    # Pulse monitoring

    def _refresh(self, pulse) -> tuple:
        """Returns (sinks_changed, apps_changed, channels_changed, sources_changed)."""
        state = {}
        sink_descriptions = {}
        for sink in pulse.sink_list():
            volume = round(pulse.volume_get_all_chans(sink) * 100)
            state[sink.name] = (volume, bool(sink.mute))
            sink_descriptions[sink.name] = sink.description
        sinks_changed = state != self.sink_state
        if sinks_changed:
            self.sink_state = state

        try:
            info = pulse.server_info()
            self.default_sink = info.default_sink_name
            self.default_source = info.default_source_name
        except Exception:
            pass
        channels_changed = self._refresh_channels(sink_descriptions)
        devices_changed = self._refresh_devices(sink_descriptions)

        src_state = {}
        descriptions = {}
        for source in pulse.source_list():
            if source.name.endswith(".monitor"):
                continue  # pavucontrol's "All Except Monitors" view
            src_state[source.name] = (round(pulse.volume_get_all_chans(source) * 100),
                                      bool(source.mute))
            descriptions[source.name] = source.description
        sources_changed = src_state != self.source_state
        if sources_changed:
            self.source_state = src_state
        sources_changed = self._refresh_sources(descriptions) or sources_changed

        groups: dict[str, dict] = {}
        for si in pulse.sink_input_list():
            props = si.proplist
            if props.get("node.name", "").endswith(".out"):
                continue  # our own channel->G3 loopback streams
            name = props.get("application.name") or props.get("application.process.binary")
            if not name:
                continue
            group = groups.setdefault(name, {"name": name, "icon_candidates": [],
                                             "volume": 0, "muted": True, "inputs": []})
            group["volume"] = max(group["volume"],
                                  round(pulse.volume_get_all_chans(si) * 100))
            group["muted"] = group["muted"] and bool(si.mute)
            group["inputs"].append(si.index)
            for prop in ("application.icon_name", "pipewire.access.portal.app_id",
                         "application.id", "application.process.binary"):
                value = props.get(prop)
                if value and value not in group["icon_candidates"]:
                    group["icon_candidates"].append(value)
        for name in groups:
            self._app_order.setdefault(name, len(self._app_order))
        apps = sorted(groups.values(), key=lambda g: self._app_order[g["name"]])
        apps_changed = apps != self.apps
        if apps_changed:
            self.apps = apps
            if self.app_offset >= len(apps):
                self.app_offset = 0
            self._app_meter_ids = {str(i) for g in apps for i in g["inputs"]}
            self._sync_meters()
        return sinks_changed, apps_changed, channels_changed, sources_changed, devices_changed

    # Input (source) discovery - mirrors channel discovery; the config file's
    # "inputs" list pins order/labels/colors, anything else is appended
    # alphabetically with the source's description as its label.

    def _refresh_sources(self, descriptions: dict) -> bool:
        config = self._load_config()
        names = set(self.source_state)
        default = self.default_source if self.default_source in names else None
        sources = []
        used = set()
        entries = {e.get("match"): e for e in config.get("inputs", []) if e.get("match")}
        if default:  # default input device first, accented
            used.add(default)
            sources.append(self._make_source(default, entries.get(default, {}),
                                             descriptions, accent=True))
        for entry in config.get("inputs", []):
            name = entry.get("match")
            if name in names and name not in used:
                used.add(name)
                sources.append(self._make_source(name, entry, descriptions))
        for name in sorted(names - used):
            sources.append(self._make_source(name, {}, descriptions))

        changed = sources != self.sources
        if changed:
            self.sources = sources
            if self.source_offset >= len(sources):
                self.source_offset = 0
            self._source_meter_targets = {s["meter_target"] for s in sources}
            self._sync_meters()
        return changed

    @staticmethod
    def _make_source(name: str, entry: dict, descriptions: dict,
                     accent: bool = False) -> dict:
        label = entry.get("label") or descriptions.get(name) or name
        color = entry.get("color") or AppSlot.app_color(name)
        return {"name": name, "label": label, "color": tuple(color)[:3],
                "meter_target": name, "accent": accent}

    def get_source(self, slot: int):
        index = self.source_offset + slot
        return self.sources[index] if 0 <= index < len(self.sources) else None

    def get_source_state(self, name: str):
        return self.source_state.get(name)

    def source_count(self) -> int:
        return len(self.sources)

    def advance_source_page(self) -> None:
        if len(self.sources) > 4:
            self.source_offset += 4
            if self.source_offset >= len(self.sources):
                self.source_offset = 0
        else:
            self.source_offset = 0
        self._notify_sources()

    def _notify_sources(self) -> None:
        with self._lock:
            targets = list(self._source_slots) + list(self._source_pagers)
        for target in targets:
            try:
                target.render()
            except Exception as e:
                log.error(f"FaderDisplay: input key render failed: {e}")

    def adjust_source_volume(self, name: str, delta: int) -> None:
        state = self.source_state.get(name)
        if state is None:
            return
        target = max(0, min(150, state[0] + delta)) / 100
        try:
            with pulsectl.Pulse("fader-display-cmd") as pulse:
                for source in pulse.source_list():
                    if source.name == name:
                        pulse.volume_set_all_chans(source, target)
        except Exception as e:
            log.error(f"FaderDisplay: source volume change failed: {e}")

    def toggle_source_mute(self, name: str) -> None:
        try:
            with pulsectl.Pulse("fader-display-cmd") as pulse:
                for source in pulse.source_list():
                    if source.name == name:
                        pulse.source_mute(source.index, not source.mute)
        except Exception as e:
            log.error(f"FaderDisplay: source mute toggle failed: {e}")

    def add_source_slot(self, slot) -> None:
        with self._lock:
            if slot not in self._source_slots:
                self._source_slots.append(slot)

    def remove_source_slot(self, slot) -> None:
        with self._lock:
            if slot in self._source_slots:
                self._source_slots.remove(slot)

    def add_source_pager(self, pager) -> None:
        with self._lock:
            if pager not in self._source_pagers:
                self._source_pagers.append(pager)

    def remove_source_pager(self, pager) -> None:
        with self._lock:
            if pager in self._source_pagers:
                self._source_pagers.remove(pager)

    # Channel discovery (Channels page)
    #
    # Channels are sinks whose names end in "_channel", plus anything pinned in
    # the optional config file, which also sets order, colors and (optionally)
    # labels — labels default to the sink description, as pavucontrol shows it:
    #   <data>/settings/fader-display.json
    #   {"channels": [{"match": "media_channel", "color": [205, 50, 45]}, ...],
    #    "master": {"color": [235, 145, 35]}}
    # Missing file or entries -> alphabetical order, name-hash colors.
    # The file is re-read when its mtime changes (checked on every refresh).

    MASTER_COLOR = (235, 145, 35)

    def _load_config(self) -> dict:
        path = os.path.join(gl.DATA_PATH, "settings", "fader-display.json")
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            mtime = None
        if mtime != self._config_mtime:
            self._config_mtime = mtime
            self._config = {}
            if mtime is not None:
                try:
                    with open(path) as f:
                        self._config = json.load(f) or {}
                except Exception as e:
                    log.warning(f"FaderDisplay: bad config {path}: {e}")
        return self._config

    def _refresh_channels(self, descriptions: dict) -> bool:
        config = self._load_config()
        sink_names = set(self.sink_state)
        channels = []
        used = set()
        for entry in config.get("channels", []):
            name = entry.get("match")
            if name in sink_names and name not in used:
                used.add(name)
                channels.append(self._make_channel(name, entry, descriptions))
        for name in sorted(sink_names):
            if name.endswith("_channel") and name not in used:
                used.add(name)
                channels.append(self._make_channel(name, {}, descriptions))

        changed = channels != self.channels
        if changed:
            self.channels = channels
            if self.channel_offset >= len(channels):
                self.channel_offset = 0
            self._channel_meter_targets = {c["meter_target"] for c in channels}
            self._sync_meters()
        return changed

    # Output-device discovery (Outputs page): every sink that is not a channel.
    # The default device is listed first and drawn with an accent ring; the
    # config's "master" color applies to whichever device is default (it keeps
    # its own label), and an "outputs" list pins order/labels/colors.
    # Only the default device is metered: tapping a sink's monitor keeps the
    # device awake, so metering every output would stop them all suspending.

    def _refresh_devices(self, descriptions: dict) -> bool:
        config = self._load_config()
        channel_names = {c["name"] for c in self.channels}
        names = set(self.sink_state) - channel_names
        default = self.default_sink if self.default_sink in names else None

        devices = []
        used = set()

        def add(name: str, entry: dict) -> None:
            used.add(name)
            is_default = name == default
            label = entry.get("label") or descriptions.get(name) or name
            if is_default:
                color = (config.get("master", {}).get("color")
                         or entry.get("color") or self.MASTER_COLOR)
            else:
                color = entry.get("color") or AppSlot.app_color(name)
            devices.append({
                "name": name, "label": label, "color": tuple(color)[:3],
                "meter_target": f"{name}.monitor" if is_default else None,
                "accent": is_default,
            })

        entries = {e.get("match"): e for e in config.get("outputs", []) if e.get("match")}
        if default:
            add(default, entries.get(default, {}))
        for entry in config.get("outputs", []):
            name = entry.get("match")
            if name in names and name not in used:
                add(name, entry)
        for name in sorted(names - used):
            add(name, {})

        changed = devices != self.sink_devices
        if changed:
            self.sink_devices = devices
            if self.device_offset >= len(devices):
                self.device_offset = 0
            self._device_meter_targets = {d["meter_target"] for d in devices
                                          if d["meter_target"]}
            self._sync_meters()
        return changed

    def get_device(self, slot: int):
        index = self.device_offset + slot
        return self.sink_devices[index] if 0 <= index < len(self.sink_devices) else None

    def get_master_channel(self):
        """The default output device's entry (kept for custom pages)."""
        for device in self.sink_devices:
            if device["accent"]:
                return device
        return None

    def device_count(self) -> int:
        return len(self.sink_devices)

    def advance_device_page(self) -> None:
        if len(self.sink_devices) > 4:
            self.device_offset += 4
            if self.device_offset >= len(self.sink_devices):
                self.device_offset = 0
        else:
            self.device_offset = 0
        self._notify_devices()

    def _notify_devices(self) -> None:
        with self._lock:
            targets = list(self._device_slots) + list(self._device_pagers)
        for target in targets:
            try:
                target.render()
            except Exception as e:
                log.error(f"FaderDisplay: output key render failed: {e}")

    def add_device_slot(self, slot) -> None:
        with self._lock:
            if slot not in self._device_slots:
                self._device_slots.append(slot)

    def remove_device_slot(self, slot) -> None:
        with self._lock:
            if slot in self._device_slots:
                self._device_slots.remove(slot)

    def add_device_pager(self, pager) -> None:
        with self._lock:
            if pager not in self._device_pagers:
                self._device_pagers.append(pager)

    def remove_device_pager(self, pager) -> None:
        with self._lock:
            if pager in self._device_pagers:
                self._device_pagers.remove(pager)

    @staticmethod
    def _make_channel(name: str, entry: dict, descriptions: dict) -> dict:
        label = entry.get("label") or descriptions.get(name) or (
            name[:-len("_channel")] if name.endswith("_channel") else name
        ).replace("_", " ").title()
        color = entry.get("color") or AppSlot.app_color(name)
        return {"name": name, "label": label, "color": tuple(color)[:3],
                "meter_target": f"{name}.out"}

    def get_channel(self, slot: int):
        index = self.channel_offset + slot
        return self.channels[index] if 0 <= index < len(self.channels) else None

    def channel_count(self) -> int:
        return len(self.channels)

    def advance_channel_page(self) -> None:
        if len(self.channels) > 4:
            self.channel_offset += 4
            if self.channel_offset >= len(self.channels):
                self.channel_offset = 0
        else:
            self.channel_offset = 0
        self._notify_channels()

    def _notify_channels(self) -> None:
        with self._lock:
            targets = list(self._channel_slots) + list(self._channel_pagers)
        for target in targets:
            try:
                target.render()
            except Exception as e:
                log.error(f"FaderDisplay: channel key render failed: {e}")

    def adjust_sink_volume(self, name: str, delta: int) -> None:
        state = self.sink_state.get(name)
        if state is None:
            return
        target = max(0, min(150, state[0] + delta)) / 100
        try:
            with pulsectl.Pulse("fader-display-cmd") as pulse:
                for sink in pulse.sink_list():
                    if sink.name == name:
                        pulse.volume_set_all_chans(sink, target)
        except Exception as e:
            log.error(f"FaderDisplay: sink volume change failed: {e}")

    def toggle_sink_mute(self, name: str) -> None:
        try:
            with pulsectl.Pulse("fader-display-cmd") as pulse:
                for sink in pulse.sink_list():
                    if sink.name == name:
                        pulse.sink_mute(sink.index, not sink.mute)
        except Exception as e:
            log.error(f"FaderDisplay: sink mute toggle failed: {e}")

    def _dispatch(self, changes: tuple) -> None:
        sinks_changed, apps_changed, channels_changed, sources_changed, devices_changed = changes
        if sinks_changed:
            self._notify()
        if apps_changed:
            self._notify_apps()
        if sinks_changed or channels_changed:
            self._notify_channels()
        if sources_changed:
            self._notify_sources()
        if sinks_changed or devices_changed:
            self._notify_devices()

    def _notify(self) -> None:
        with self._lock:
            segments = list(self._segments)
        for segment in segments:
            try:
                segment.render()
            except Exception as e:
                log.error(f"FaderDisplay: segment render failed: {e}")

    def _monitor(self) -> None:
        def on_event(ev):
            raise pulsectl.PulseLoopStop

        while True:
            try:
                with pulsectl.Pulse("fader-display-monitor") as pulse:
                    self._dispatch(self._refresh(pulse))
                    pulse.event_mask_set("sink", "sink_input", "source", "server")
                    pulse.event_callback_set(on_event)
                    while True:
                        pulse.event_listen(timeout=10)
                        self._dispatch(self._refresh(pulse))
            except Exception as e:
                log.warning(f"FaderDisplay: pulse monitor reconnecting: {e}")
                time.sleep(3)

    # Media (MPRIS) monitoring

    def _notify_media(self) -> None:
        with self._lock:
            keys = list(self._media_keys)
        for key in keys:
            try:
                key.render()
            except Exception as e:
                log.error(f"FaderDisplay: media key render failed: {e}")

    def _media_monitor(self) -> None:
        # playerctl --follow prints the current status immediately, then one
        # line per change; an empty line means no player is available.
        while True:
            try:
                # cwd="/": flatpak-spawn --host replays the caller's cwd on the host,
                # and the sandbox's /app/... paths don't exist there
                proc = subprocess.Popen(
                    ["flatpak-spawn", "--host", "playerctl", "--follow", "status"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd="/",
                )
                for line in proc.stdout:
                    status = line.strip() or None
                    if status != self.media_status:
                        self.media_status = status
                        self._notify_media()
                proc.wait()
            except Exception as e:
                log.warning(f"FaderDisplay: media monitor restarting: {e}")
            if self.media_status is not None:
                self.media_status = None
                self._notify_media()
            time.sleep(5)

    # Level metering
    #
    # Each segment may carry a "meter-target" setting naming a PipeWire node to
    # meter: a loopback output stream ("media_channel.out", captured with
    # pw-record, post-fader) or a hardware sink monitor ("<sink>.monitor",
    # captured with parec - pw-record can't tap hardware sink monitors and
    # parec can't tap stream nodes, so both are needed). Virtual-sink monitors
    # are silent on this PipeWire, hence the .out stream taps.

    METER_INTERVAL = 1 / 15  # display refresh period (s)
    METER_RELEASE = 0.86     # bar decay factor per tick (fast attack, slow release)
    METER_HOLD_S = 1.2     # peak-hold time before the marker collapses
    METER_STALE_S = 0.5    # no samples for this long -> treat as silence

    def get_meter_display(self, target: str) -> tuple:
        return self.meter_display.get(target, ((0.0, 0.0), (0.0, 0.0)))

    def _sync_meters(self) -> None:
        """Start/stop capture threads to match the registered segments."""
        with self._lock:
            segments = list(self._segments)
        wanted = set()
        for seg in segments:
            try:
                target = seg.get_settings().get("meter-target")
            except Exception:
                target = None
            if target:
                wanted.add(target)
        wanted |= self._app_meter_ids       # per-app stream taps (numeric node ids)
        wanted |= self._channel_meter_targets  # discovered channels
        wanted |= self._source_meter_targets   # discovered inputs (mics, line-ins)
        wanted |= self._device_meter_targets   # default output device

        for target in wanted:
            if target not in self._meters:
                self._meters[target] = _MeterState()
                threading.Thread(target=self._meter_reader, args=(target,),
                                 daemon=True, name=f"meter-{target[-20:]}").start()
                if not self._meter_display_started:
                    self._meter_display_started = True
                    threading.Thread(target=self._meter_display_loop,
                                     daemon=True, name="meter-display").start()

        for target in list(self._meters):
            if target not in wanted:
                state = self._meters.pop(target)
                state.wanted = False
                if state.proc is not None:
                    try:
                        state.proc.kill()
                    except Exception:
                        pass
                self.meter_display.pop(target, None)

    def _meter_reader(self, target: str) -> None:
        """Hold one capture pipe open and accumulate stereo peaks."""
        state = self._meters.get(target)
        if state is None:
            return
        # name the capture streams so they're identifiable in pavucontrol's
        # Recording tab instead of showing as "pw-cat unknown input"
        if target.endswith(".monitor"):
            cmd = ["flatpak-spawn", "--host", "parec", f"--device={target}",
                   "--client-name=FaderDisplay",
                   f"--stream-name=meter: {target}",
                   "--format=float32le", "--rate=8000", "--channels=2",
                   "--raw", "--latency-msec=50"]
        else:
            props = ('{ media.name = "FaderDisplay meter: ' + target +
                     '" application.name = "FaderDisplay" }')
            cmd = ["flatpak-spawn", "--host", "pw-record", "--target", target,
                   "-P", props,
                   "--format", "f32", "--rate", "8000", "--channels", "2", "-"]
        while state.wanted:
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, cwd="/")
                state.proc = proc
                while state.wanted:
                    chunk = proc.stdout.read(3200)  # 50 ms of stereo f32 @ 8 kHz
                    if not chunk:
                        break
                    vals = array("f", chunk[:len(chunk) // 8 * 8])
                    if not vals:
                        continue
                    peak_l = max(abs(v) for v in vals[0::2])
                    peak_r = max(abs(v) for v in vals[1::2])
                    with self._lock:
                        state.raw_max[0] = max(state.raw_max[0], peak_l)
                        state.raw_max[1] = max(state.raw_max[1], peak_r)
                        state.last_data = time.time()
            except Exception as e:
                log.warning(f"FaderDisplay: meter capture for {target} restarting: {e}")
            if state.wanted:
                time.sleep(3)

    def _meter_display_loop(self) -> None:
        renders = 0
        render_time = 0.0
        last_report = time.time()
        while True:
            time.sleep(self.METER_INTERVAL)
            now = time.time()
            for target, state in list(self._meters.items()):
                with self._lock:
                    raw = list(state.raw_max)
                    state.raw_max[0] = state.raw_max[1] = 0.0
                    stale = now - state.last_data > self.METER_STALE_S
                if stale:
                    raw = [0.0, 0.0]
                bars, holds = [], []
                for ch in (0, 1):
                    level = max(raw[ch], state.level[ch] * self.METER_RELEASE)
                    state.level[ch] = level if level >= 1e-4 else 0.0
                    if raw[ch] > state.hold[ch]:
                        state.hold[ch] = raw[ch]
                        state.hold_ts[ch] = now
                    elif now - state.hold_ts[ch] > self.METER_HOLD_S:
                        state.hold[ch] = state.level[ch]
                    bars.append(_amp_to_frac(state.level[ch]))
                    holds.append(_amp_to_frac(state.hold[ch]))
                self.meter_display[target] = (tuple(bars), tuple(holds))

            with self._lock:
                segments = (list(self._segments) + list(self._app_slots)
                            + list(self._channel_slots) + list(self._source_slots)
                            + list(self._device_slots))
            for seg in segments:
                try:
                    px = seg.meter_px()
                    if px is None or px == getattr(seg, "_last_meter_px", None):
                        continue
                    seg._last_meter_px = px
                    t0 = time.time()
                    seg.render()
                    render_time += time.time() - t0
                    renders += 1
                except Exception as e:
                    log.error(f"FaderDisplay: meter render failed: {e}")

            if now - last_report >= 10:
                if renders:
                    log.debug(f"FaderDisplay meter: {renders} key renders in "
                              f"{now - last_report:.0f}s, avg {1000 * render_time / renders:.1f} ms")
                renders = 0
                render_time = 0.0
                last_report = now

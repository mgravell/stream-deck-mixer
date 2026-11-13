#!/usr/bin/env bash
# Restore the PipeWire virtual-channel mixer + Stream Deck setup.
# Safe to re-run; see README.md for the two manual follow-up steps.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> PipeWire virtual channels"
mkdir -p ~/.config/pipewire/pipewire.conf.d
cp pipewire/10-virtual-channels.conf ~/.config/pipewire/pipewire.conf.d/
systemctl --user restart pipewire pipewire-pulse wireplumber
sleep 2
pactl list sinks short | grep -E 'media_channel|chat_channel|game_channel' \
  && echo "    virtual sinks up" \
  || { echo "    ERROR: virtual sinks missing"; exit 1; }

command -v playerctl >/dev/null \
  || echo "    WARNING: playerctl not installed (sudo apt install playerctl) - media keys won't work"

echo "==> StreamController (Flatpak)"
flatpak info com.core447.StreamController >/dev/null 2>&1 \
  || flatpak install -y flathub com.core447.StreamController

DATA=~/.var/app/com.core447.StreamController/data
mkdir -p "$DATA/pages" "$DATA/plugins" "$DATA/settings"

echo "==> Mixer page + FaderDisplay plugin"
cp streamcontroller/pages/*.json "$DATA/pages/"
rm -rf "$DATA/plugins/com_marc_FaderDisplay"
cp -r streamcontroller/plugins/com_marc_FaderDisplay "$DATA/plugins/"

echo "==> Channel order/colors config (optional; delete for automatic)"
cp streamcontroller/settings/fader-display.json "$DATA/settings/"

echo "==> Default-page mapping (merged, other decks preserved)"
python3 - "$DATA" <<'PY'
import json, os, sys
data = sys.argv[1]
src = json.load(open("streamcontroller/settings/pages.json"))
dst_path = os.path.join(data, "settings", "pages.json")
try:
    dst = json.load(open(dst_path))
except (FileNotFoundError, json.JSONDecodeError):
    dst = {}
dst.setdefault("default-pages", {}).update(src.get("default-pages", {}))
json.dump(dst, open(dst_path, "w"), indent=4)
PY

cat <<'EOF'

Done. Remaining manual step:
  Route each app to its channel once in pavucontrol (Playback tab).

If using a different Stream Deck, update the serial in:
  ~/.var/app/com.core447.StreamController/data/settings/pages.json
EOF

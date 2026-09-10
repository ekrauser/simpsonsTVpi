#!/usr/bin/env bash
# Apply the hardware-side boot config from the Withrow Waveshare build guide:
# screen overlay, PWM audio on GPIO 18/19, quiet boot, no screen blanking.
#
# Run ONCE on a freshly imaged Raspberry Pi OS Buster Lite card (as pi, with
# sudo available), then reboot. Safe to re-run; it won't duplicate lines.
#
# Put the Waveshare 28DPIB_DTBO.zip contents (the .dtbo files) in
# boot/overlays/ in this repo first, so a rebuild needs nothing from the web.
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

BOOT=/boot
[ -d /boot/firmware ] && [ -f /boot/firmware/config.txt ] && BOOT=/boot/firmware

echo "== overlays"
if ls "$HERE"/boot/overlays/*.dtbo >/dev/null 2>&1; then
    sudo cp "$HERE"/boot/overlays/*.dtbo "$BOOT/overlays/"
else
    echo "   no .dtbo files in $HERE/boot/overlays - copy the Waveshare ones there" >&2
fi

echo "== $BOOT/config.txt"
if ! grep -q '^# simpsonstv' "$BOOT/config.txt"; then
    sudo tee -a "$BOOT/config.txt" >/dev/null <<'EOF'

# simpsonstv - Waveshare 2.8" DPI screen + PWM audio (from withrow.io build guide)
gpio=0-9=a2
gpio=12-17=a2
gpio=20-25=a2
dtoverlay=dpi24
enable_dpi_lcd=1
display_default_lcd=1
extra_transpose_buffer=2
dpi_group=2
dpi_mode=87
dpi_output_format=0x7F216
hdmi_timings=480 0 26 16 10 640 0 25 10 15 0 0 0 60 0 32000000 1
dtoverlay=waveshare-28dpi-3b-4b
dtoverlay=waveshare-28dpi-3b
dtoverlay=waveshare-28dpi-4b
display_rotate=1
dtparam=audio=on
dtoverlay=audremap,enable_jack,pins_18_19
EOF
fi

echo "== $BOOT/cmdline.txt"
sudo cp -n "$BOOT/cmdline.txt" "$BOOT/cmdline.txt.orig"
sudo sed -i \
    -e 's/console=tty1/console=tty3/' \
    -e 's/ fsck.repair=yes//' \
    "$BOOT/cmdline.txt"
for opt in consoleblank=0 logo.nologo quiet splash; do
    grep -qw "$opt" "$BOOT/cmdline.txt" || sudo sed -i "1s/\$/ $opt/" "$BOOT/cmdline.txt"
done

echo "== /etc/rc.local"
if ! grep -q 'raspi-gpio set 18 op dl' /etc/rc.local; then
    sudo sed -i '/^exit 0/i raspi-gpio set 18 op dl\nraspi-gpio set 19 op a5' /etc/rc.local
fi

echo "== done; reboot to apply"

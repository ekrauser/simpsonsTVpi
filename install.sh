#!/usr/bin/env bash
# Install / re-install the Simpsons TV software on a Raspberry Pi.
# Safe to re-run; every step is idempotent.
#
#   ./install.sh                                 scripts + services only
#   ./install.sh --smb //nas/media/simpsonstv --smb-user bob      + SMB mount
#   ./install.sh --nfs nas:/volume1/media/simpsonstv              + NFS mount
#   ./install.sh --auto-update                   also enable nightly git pull
#   ./install.sh --mqtt homeassistant.local --mqtt-user tv   Home Assistant via MQTT
#
# Run as the 'pi' user (it uses sudo where needed).
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
MOUNT_POINT=/mnt/simpsonstv
SMB="" SMB_USER="" NFS="" AUTO_UPDATE=0 MQTT="" MQTT_USER="" MQTT_PASS=""

while [ $# -gt 0 ]; do
    case "$1" in
        --smb)         SMB="$2"; shift 2 ;;
        --smb-user)    SMB_USER="$2"; shift 2 ;;
        --nfs)         NFS="$2"; shift 2 ;;
        --mount-point) MOUNT_POINT="$2"; shift 2 ;;
        --auto-update) AUTO_UPDATE=1; shift ;;
        --mqtt)        MQTT="$2"; shift 2 ;;
        --mqtt-user)   MQTT_USER="$2"; shift 2 ;;
        --mqtt-pass)   MQTT_PASS="$2"; shift 2 ;;
        -h|--help)     sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "Packages"
# Buster is end-of-life; its packages moved to legacy.raspbian.org. Repoint a
# stock sources.list so apt still works on the guide's image.
if grep -q "raspbian.raspberrypi.org" /etc/apt/sources.list 2>/dev/null; then
    sudo sed -i.bak "s#http://raspbian.raspberrypi.org/raspbian#http://legacy.raspbian.org/raspbian#" /etc/apt/sources.list
fi
sudo apt-get update -qq || echo "WARNING: apt-get update failed; trying to install with the current lists" >&2
sudo apt-get install -y -qq python3 python3-rpi.gpio python3-paho-mqtt omxplayer raspi-gpio git cifs-utils nfs-common

say "Config"
[ -f "$HERE/config.json" ] || cp "$HERE/config.example.json" "$HERE/config.json"
mkdir -p "$HERE/state" "$HERE/videos"
chmod +x "$HERE"/*.py "$HERE/tvctl" "$HERE/update.sh"
sudo ln -sf "$HERE/tvctl" /usr/local/bin/tvctl

if [ -n "$MQTT" ]; then
    say "MQTT -> $MQTT"
    if [ -n "$MQTT_USER" ] && [ -z "$MQTT_PASS" ]; then
        read -rsp "MQTT password for $MQTT_USER: " MQTT_PASS; echo
    fi
    python3 - "$HERE/config.json" "$MQTT" "$MQTT_USER" "$MQTT_PASS" <<'PY'
import json, sys
path, host, user, pw = sys.argv[1:]
cfg = json.load(open(path))
mq = cfg.setdefault("remote", {}).setdefault("mqtt", {})
if ":" in host:
    host, port = host.rsplit(":", 1)
    mq["port"] = int(port)
mq["host"] = host
if user:
    mq["username"], mq["password"] = user, pw
json.dump(cfg, open(path, "w"), indent=2)
print("config.json: remote.mqtt.host = %s" % host)
PY
fi

if [ -n "$SMB" ]; then
    say "SMB mount $SMB -> $MOUNT_POINT"
    CRED=/etc/simpsonstv.cred
    if [ ! -f "$CRED" ]; then
        [ -n "$SMB_USER" ] || read -rp "SMB username: " SMB_USER
        read -rsp "SMB password for $SMB_USER: " SMB_PASS; echo
        printf 'username=%s\npassword=%s\n' "$SMB_USER" "$SMB_PASS" | sudo tee "$CRED" >/dev/null
        sudo chmod 600 "$CRED"
    fi
    sudo mkdir -p "$MOUNT_POINT"
    LINE="$SMB $MOUNT_POINT cifs credentials=$CRED,ro,uid=pi,gid=pi,iocharset=utf8,vers=3.0,noauto,x-systemd.automount,x-systemd.idle-timeout=300,x-systemd.mount-timeout=15,_netdev 0 0"
    grep -qF " $MOUNT_POINT " /etc/fstab && sudo sed -i "\# $MOUNT_POINT #d" /etc/fstab
    echo "$LINE" | sudo tee -a /etc/fstab >/dev/null
fi

if [ -n "$NFS" ]; then
    say "NFS mount $NFS -> $MOUNT_POINT"
    sudo mkdir -p "$MOUNT_POINT"
    LINE="$NFS $MOUNT_POINT nfs ro,soft,timeo=50,retrans=3,noauto,x-systemd.automount,x-systemd.idle-timeout=300,x-systemd.mount-timeout=15,_netdev 0 0"
    grep -qF " $MOUNT_POINT " /etc/fstab && sudo sed -i "\# $MOUNT_POINT #d" /etc/fstab
    echo "$LINE" | sudo tee -a /etc/fstab >/dev/null
fi

if [ -n "$SMB$NFS" ]; then
    sudo systemctl daemon-reload
    UNIT="$(systemd-escape -p --suffix=automount "$MOUNT_POINT")"
    sudo systemctl restart "$UNIT" || true
    if ls "$MOUNT_POINT" >/dev/null 2>&1; then
        echo "mounted: $(find "$MOUNT_POINT" -maxdepth 2 -iname '*.mp4' | wc -l) mp4 files visible"
    else
        echo "WARNING: $MOUNT_POINT is not reachable yet; the player will keep retrying" >&2
    fi
fi

say "Services"
for unit in tvplayer.service tvbutton.service tvremote.service tvupdate.service tvupdate.timer; do
    sed "s#__DIR__#$HERE#g" "$HERE/systemd/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable tvplayer.service tvbutton.service tvremote.service
sudo systemctl restart tvbutton.service tvplayer.service tvremote.service
if [ "$AUTO_UPDATE" = 1 ]; then
    sudo systemctl enable --now tvupdate.timer
fi

say "Done"
"$HERE/tvctl" list || true
echo
echo "tvctl status | tvctl list | tvctl set halloween | tvctl auto | tvctl next | tvctl log"
echo "web remote:  http://$(hostname).local:$(python3 -c 'import json;print(json.load(open("'"$HERE"'/config.json")).get("remote",{}).get("http_port",8080))')/"

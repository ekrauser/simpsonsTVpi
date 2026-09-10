# Simpsons TV

Software for the [Withrow Simpsons TV (Waveshare version)](https://withrow.io/simpsons-tv-build-guide-waveshare).
Same hardware, same SD card full of episodes, plus:

- **Holiday playlists that switch by date.** Treehouse of Horror in October,
  Christmas episodes in December, the whole library the rest of the year.
- **Home Assistant control.** A "Simpsons TV" device with a playlist select
  and a skip button appears in HA by itself (MQTT discovery). There's also a
  phone-sized web remote and a plain HTTP API.
- **Everything the Pi runs is in git.** Scripts, playlists, systemd units,
  the boot-config edits from the guide. `update.sh` pulls and restarts.

The Pi Zero W, Buster Lite and omxplayer stay as the guide has them.

```
player.py          picks the playlist (schedule or override), shuffles, plays on loop
buttons.py         front power button -> screen on/off (same as the guide)
remote.py          web remote, HTTP API, Home Assistant via MQTT   (tvremote.service)
control.py         set / auto / skip / status, shared by tvctl and remote.py
tvctl              shell front end for the above
playlists/         one .txt per playlist + schedule.json
config.json        per-Pi settings, gitignored (copied from config.example.json)
install.sh         packages, config, services. Safe to re-run.
update.sh          git pull + restart services
setup-boot.sh      config.txt / cmdline.txt / rc.local edits from the guide (fresh image only)
videos/            the episodes (gitignored)
tools/             optional: encode.py, plex_export.py
tests/             python3 -m unittest discover tests
```

## Install on the existing TV

The guide's clone lives at `~/simpsonstv` with the episodes in
`~/simpsonstv/videos`. Rename it, clone this repo in its place, move the
videos folder across (a rename, instant, nothing copied):

```sh
mv ~/simpsonstv ~/simpsonstv-old
git clone https://github.com/ekrauser/simpsonsTVpi ~/simpsonstv
mv ~/simpsonstv-old/videos ~/simpsonstv/videos
cd ~/simpsonstv
./install.sh --mqtt <broker host> --mqtt-user <mqtt user>     # prompts for the password
tvctl list
```

`install.sh` installs `python3-paho-mqtt`, creates `config.json`, writes
the broker into it, and installs and starts three services: `tvplayer`,
`tvbutton`, `tvremote`. They replace the guide's `tvplayer` / `tvbutton`
units. Re-run it any time; every step is idempotent. Leave the `--mqtt`
flags off to skip Home Assistant and keep only the web remote.

`tvctl list` should show real episode counts. If the holiday lists show 0,
the filenames don't carry `S05E05`-style codes; see Playlists below.

Once `~/simpsonstv-old` has nothing else you want, delete it.

## Home Assistant

Prerequisite: an MQTT broker HA is connected to (normally the Mosquitto
add-on plus the MQTT integration). Give the Pi a broker login and pass it
to `install.sh --mqtt`.

Within a few seconds of `tvremote` starting, Settings > Devices shows
**Simpsons TV** with:

| entity | |
|---|---|
| `select.simpsonstv_playlist` | `auto` = follow the date schedule; any other option forces that playlist until you pick `auto` again. Survives reboots. |
| `button.simpsonstv_next` | skip the current episode |
| `sensor.simpsonstv_active` | the playlist actually playing right now (schedule result or override) |
| `sensor.simpsonstv_now_playing` | current episode filename |

Nothing is configured on the HA side; it's MQTT discovery. Playlist files
added to the repo show up as new options after `update.sh`. The entities go
unavailable when the Pi is off.

Example automation:

```yaml
alias: Simpsons TV - party mode
trigger:
  - platform: state
    entity_id: input_boolean.party_mode
    to: "on"
action:
  - service: select.select_option
    target: { entity_id: select.simpsonstv_playlist }
    data: { option: halloween }
```

Raw topics, if you want to bypass the entities: `simpsonstv/playlist/set`
(command: `auto` or a name), `simpsonstv/next` (any payload),
`simpsonstv/playlist`, `simpsonstv/active`, `simpsonstv/now_playing`,
`simpsonstv/availability`. The prefix is `remote.mqtt.prefix` in
`config.json`.

Troubleshooting on the Pi: `journalctl -u tvremote -n 50`. It logs
`mqtt: connected` on success and the return code on failure.

## Web remote and HTTP API

`http://raspberrypi.local:8080/` is a one-screen remote for a phone
bookmark: current playlist, now playing, a Next button, a playlist
dropdown. The API behind it, LAN only, no auth:

```
GET  /api/status             {"active": "halloween", "mode": "manual", "override": "halloween", "now_playing": "...", "playlists": [...]}
POST /api/next
POST /api/playlist/NAME      NAME = auto to follow the schedule again
POST /api/auto
GET  /api/playlists
```

GET works for the actions too. Port is `remote.http_port` in `config.json`.

## Playlists

One file per playlist in `playlists/`, one entry per line:

```
S05E05              # an episode
S03                 # a whole season
S01E01 - S09E25     # a range
*treehouse*         # a filename glob (case-insensitive)
```

Codes you don't have are skipped silently. `all` is built in and means
every file found. Shipped: `halloween` (Treehouse of Horror I to XXXV),
`christmas`, `thanksgiving`, `golden-era` (seasons 1 to 9). They're starter
lists; edit them.

Files are matched by the `S05E05` (or `5x05`) code in the filename. Files
without a code still play in `all` and match globs, but can't be picked by
code.

`playlists/schedule.json` maps dates to playlists. Rules are checked top to
bottom, first match wins, dates are `MM-DD` inclusive and may wrap past New
Year. `mix_in` / `mix_ratio` sprinkle in episodes from another playlist so
a month of the same 30 Treehouse episodes doesn't wear thin:

```json
{ "playlist": "halloween", "from": "10-01", "to": "10-31", "mix_in": "all", "mix_ratio": 0.25 }
```

The player re-reads the schedule and the override between episodes, so a
date change or an HA select takes effect at the next episode (or
immediately, since select/next also skip the current one).

Editing workflow: change a file, commit, push to `main`, then on the Pi
`./update.sh` (or enable the nightly timer with `install.sh --auto-update`).

## On the Pi

```
tvctl status          active playlist and why, now playing, media dirs
tvctl list            playlists with how many episodes each resolves to
tvctl set halloween   force a playlist (same as the HA select)
tvctl auto            back to the schedule
tvctl next            skip
tvctl show christmas  the files a playlist resolves to
tvctl log             follow the player log
tvctl restart         restart tvplayer, tvbutton, tvremote
```

Logs: `journalctl -u tvplayer`, `-u tvremote`, `-u tvbutton`.

## Config

`config.json` (created by `install.sh` from `config.example.json`):

| key | default | |
|---|---|---|
| `media_dirs` | `["videos", "/mnt/simpsonstv"]` | searched in order; a missing dir is ignored. Only `videos` matters for the SD-card setup |
| `player_cmd` | omxplayer with the guide's flags | `{file}` is the path |
| `rescan_seconds` | 300 | how often to re-list the media dirs |
| `retry_seconds` | 15 | wait when nothing is playable |
| `remote.http_port` | 8080 | web remote / API port |
| `remote.mqtt.host` | empty | broker host; empty disables MQTT. Also `port`, `username`, `password`, `prefix`, `discovery_prefix`, `device_name` |

## Rebuilding from a dead SD card

Image **2020-02-13-raspbian-buster-lite**, add `wpa_supplicant.conf` and an
empty `ssh` file to the boot partition as the guide says, boot, ssh in:

```sh
sudo apt-get install -y git
git clone https://github.com/ekrauser/simpsonsTVpi ~/simpsonstv
cd ~/simpsonstv
./setup-boot.sh && sudo reboot        # screen overlay, PWM audio, quiet boot
./install.sh --mqtt ... --mqtt-user ...
# then put the episodes back in ~/simpsonstv/videos
```

`setup-boot.sh` needs the Waveshare `.dtbo` overlay files from
[28DPIB_DTBO.zip](https://www.waveshare.com/wiki/File:28DPIB_DTBO.zip) in
`boot/overlays/` in this repo. Commit them so a rebuild needs nothing from
the web. The episodes themselves are the one thing not in git; keep a copy
of `videos/` somewhere.

## Optional: network media and encoding

Not used in the current setup, but supported:

- **Stream from a share.** `install.sh --smb //nas/share --smb-user X` or
  `--nfs nas:/path` adds a systemd automount at `/mnt/simpsonstv`, which is
  already second in `media_dirs`. Same episode code in both places: the
  first dir listed wins.
- **Play Plex originals directly.** The Zero hardware-decodes H.264 up to
  1080p; HEVC and 10-bit won't play. Test with
  `omxplayer --no-osd --aspect-mode fill FILE` before relying on it.
- **`tools/encode.py`** transcodes a folder to 480p H.264 baseline + AAC on
  a real computer, skipping files already in `--dst`. For episodes the
  card is missing, e.g. newer Treehouse specials.
- **`tools/plex_export.py`** turns a Plex playlist into a `playlists/*.txt`
  file (`PLEX_URL`, `PLEX_TOKEN`, then `export "Playlist name"`).

## Notes

- No RTC on the Zero; the date comes from NTP once wifi is up, otherwise
  the last saved time. Fine for month-wide windows.
- `python3 -m unittest discover tests` covers playlists, schedule, library
  scanning, the control operations, the HTTP API, and the HA discovery
  payloads, with a fake player. Run it on a laptop before pushing.

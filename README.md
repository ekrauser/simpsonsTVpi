# Simpsons TV, networked

Software for the [Withrow Simpsons TV (Waveshare version)](https://withrow.io/simpsons-tv-build-guide-waveshare),
reworked so that:

- **Episodes live on your NAS, not the SD card.** The Pi mounts a share
  (SMB or NFS) and plays straight off it. A local `videos/` folder is an
  optional offline fallback.
- **Playlists switch by date.** Treehouse of Horror in October, Christmas
  episodes in December, whatever you like. Or force one with `tvctl set`.
- **Plex is the playlist editor.** Build a playlist in Plex, export it to a
  text file with one command, commit it.
- **Everything the Pi needs is in this repo.** Scripts, playlists, systemd
  units, the boot-config edits from the guide, and a one-line updater. If
  the SD card dies you re-flash, clone, run two scripts, done.

The hardware, wiring and case are unchanged from the guide. The Pi Zero W,
Buster Lite and omxplayer are still the recommended combo: the Zero can only
hardware-decode H.264 at low resolution, and omxplayer is the only player
that does that well on it.

```
player.py          picks a playlist by date/override, shuffles, plays on loop
buttons.py         power button -> screen on/off (same as the guide)
tvctl              status / list / set / auto / next / log / restart
playlists/         one .txt per playlist + schedule.json
config.json        per-Pi settings (copied from config.example.json)
install.sh         installs packages, mount, services. Re-runnable.
setup-boot.sh      the config.txt / cmdline.txt / rc.local edits from the guide
update.sh          git pull + restart, by hand or nightly
tools/encode.py    run on your desktop/NAS: Plex library -> 480p files
tools/plex_export.py  Plex playlist -> playlists/NAME.txt
```

## 1. Get the episodes onto the share

**Already have encoded episodes on the Pi's SD card?** They're in the right
format already. Copy them to the share once and skip the encoder, or use it
only to fill gaps (it never touches files that already exist in `--dst`):

```sh
# from your desktop, straight from the running Pi (slow over wifi; leave it overnight)
scp -r pi@raspberrypi.local:simpsonstv/videos/ /path/to/share/simpsonstv/
# or pull the SD card and copy the folder from its ext4 root partition
```

Check the filenames contain `S05E05`-style codes (`ls | head`); the playlists
match on those. Files without codes still play in `all` and match globs like
`*treehouse*`, they just can't be picked by code.

**Encoding from Plex.** The Pi can't play what Plex has (1080p, x265, whatever). It needs 480p H.264
baseline. Run the encoder on your desktop or NAS, pointed at the folder Plex
already indexes and at the folder you'll share to the Pi:

```sh
python3 tools/encode.py --src "/volume1/video/TV/The Simpsons" --dst /volume1/media/simpsonstv
```

It's incremental (skips files already in `--dst`), keeps filenames so the
`S05E05` codes survive, mirrors season folders, and runs several ffmpeg jobs
in parallel. Add `--dry-run` to see what it would do. Re-run it whenever new
episodes land in Plex.

Share `/volume1/media/simpsonstv` read-only over SMB (or NFS) to the Pi.

## 2. Set up the Pi

Fresh card: image **2020-02-13-raspbian-buster-lite**, drop `wpa_supplicant.conf`
and an empty `ssh` file on the boot partition as the guide says, boot, ssh in.

```sh
sudo apt-get install -y git
git clone https://github.com/ekrauser/simpsonsTVpi ~/simpsonstv
cd ~/simpsonstv

# once, on a fresh image: the screen/audio/quiet-boot config from the guide
# (put the Waveshare .dtbo files in boot/overlays/ first, see below)
./setup-boot.sh && sudo reboot

# packages, config, mount, systemd services
./install.sh --smb //nas/media/simpsonstv --smb-user pi-tv
# or: ./install.sh --nfs nas:/volume1/media/simpsonstv
# add --auto-update to git-pull nightly at 04:30
```

`install.sh` is safe to re-run. It writes the mount to `/etc/fstab` as a
systemd automount, so a NAS that's off at boot doesn't hang the Pi; the
player just keeps retrying and plays whatever's in `videos/` meanwhile.

Already have a working Pi from the guide? Skip `setup-boot.sh`, run
`install.sh`, then `sudo systemctl disable` the old `tvplayer`/`tvbutton`
services if they pointed somewhere else (install.sh overwrites units of the
same name, so usually there's nothing to do). Move any episodes you want
offline into `videos/`, or leave it empty.

**Waveshare overlays.** The `.dtbo` files from Waveshare's
[28DPIB_DTBO.zip](https://www.waveshare.com/wiki/File:28DPIB_DTBO.zip) go in
`boot/overlays/` in this repo. Commit them; they're small, and then a rebuild
needs nothing from the web.

## 3. Playlists

One file per playlist in `playlists/`, one entry per line:

```
S05E05              # an episode
S03                 # a whole season
S01E01 - S09E25     # a range
*treehouse*         # a filename glob (case-insensitive)
```

Codes you don't have are skipped silently, so lists can be aspirational.
`all` is built in and means everything found. The shipped lists
(`halloween`, `christmas`, `thanksgiving`, `golden-era`) are starters; edit
them.

`playlists/schedule.json` maps dates to playlists, first match wins, dates
wrap across New Year. `mix_in` / `mix_ratio` sprinkle in episodes from
another playlist so a month of the same 30 Treehouse episodes doesn't wear
thin:

```json
{ "playlist": "halloween", "from": "10-01", "to": "10-31", "mix_in": "all", "mix_ratio": 0.25 }
```

The player re-checks the schedule between episodes, so the switch happens
by itself at midnight.

### From Plex

```sh
export PLEX_URL=http://plex.local:32400 PLEX_TOKEN=...   # see plex_export.py --help for the token
python3 tools/plex_export.py list
python3 tools/plex_export.py export "Simpsons - Christmas" > playlists/christmas.txt
git commit -am "christmas playlist from plex" && git push
```

Then on the Pi: `./update.sh` (or wait for the nightly timer).

## 4. Day to day

```
tvctl status          what's playing, which playlist, why, and whether the mount is up
tvctl list            playlists with how many episodes each resolves to right now
tvctl set halloween   force a playlist (persists across reboots), skips ahead
tvctl auto            back to the schedule
tvctl next            skip this episode
tvctl show christmas  the actual files a playlist resolves to
tvctl log             follow the player log
```

## Config

`config.json` (gitignored, created by install.sh from `config.example.json`):

| key | default | |
|---|---|---|
| `media_dirs` | `["videos", "/mnt/simpsonstv"]` | searched in order; first dir wins on duplicate codes |
| `player_cmd` | omxplayer with the guide's flags | `{file}` is the path. Swap in `cvlc`/`mpv` on a newer OS |
| `rescan_seconds` | 300 | how often to re-list the media dirs |
| `retry_seconds` | 15 | wait when nothing is playable |

## Notes

- **Bandwidth.** 480p at crf 23 is around 1 to 2 Mbit/s. A Pi Zero W on
  2.4 GHz manages 15 to 25, so streaming is comfortable. If you ever see
  stutter, check the Pi's wifi signal before anything else.
- **Clock.** The Zero has no RTC; the date comes from NTP once wifi is up.
  If it boots with no network it uses the last saved time, which is close
  enough for a month-wide window.
- **Tests.** `python3 -m unittest discover tests` runs the playlist,
  schedule and library tests plus a full player-loop smoke test with a fake
  player, so you can change `player.py` on a laptop with confidence.
- **Why not stream from Plex directly?** The Pi would have to ask Plex to
  transcode every episode on the fly, and omxplayer's HLS support is
  unreliable. Pre-encoding once onto the share is simpler and more robust.

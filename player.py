#!/usr/bin/env python3
"""
Simpsons TV player.

Plays episodes on loop, picking a playlist by date (Halloween, Christmas, ...)
or by manual override, from any mix of local folders and network mounts.

Runs on Python 3.5+ (Raspberry Pi OS Buster ships 3.7). No third-party deps.

    player.py                 run forever (what the systemd service does)
    player.py --status        show which playlist is active and why
    player.py --list          list playlists with how many episodes resolve
    player.py --resolve NAME  print the files a playlist resolves to
    player.py --once          play exactly one episode, then exit
"""
import argparse
import datetime
import fnmatch
import json
import logging
import os
import random
import re
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))

DEFAULT_CONFIG = {
    # Searched in order. If the same episode code exists in more than one,
    # the first directory listed wins. Relative paths are relative to this file.
    "media_dirs": ["videos", "/mnt/simpsonstv"],
    "playlist_dir": "playlists",
    "schedule_file": "playlists/schedule.json",
    "state_dir": "state",
    "extensions": [".mp4", ".mkv", ".m4v", ".mov"],
    # {file} is replaced with the path to play.
    "player_cmd": ["omxplayer", "--no-osd", "--aspect-mode", "fill", "{file}"],
    # Re-scan the media dirs at most this often (seconds).
    "rescan_seconds": 300,
    # Seconds between retries when nothing is playable (mount down, empty).
    "retry_seconds": 15,
}

CODE_RE = re.compile(r"(?<![A-Za-z0-9])[Ss](\d{1,2})\s*[Ee](\d{1,3})(?![0-9])")
ALT_CODE_RE = re.compile(r"(?<![0-9])(\d{1,2})x(\d{2,3})(?![0-9])")
SEASON_RE = re.compile(r"^[Ss](\d{1,2})$")
RANGE_RE = re.compile(r"^(\S+)\s*-\s*(\S+)$")

log = logging.getLogger("tvplayer")


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if path and os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
        cfg.update(user)
    cfg["media_dirs"] = [_abs(p) for p in cfg["media_dirs"]]
    cfg["playlist_dir"] = _abs(cfg["playlist_dir"])
    cfg["schedule_file"] = _abs(cfg["schedule_file"])
    cfg["state_dir"] = _abs(cfg["state_dir"])
    cfg["extensions"] = tuple(e.lower() for e in cfg["extensions"])
    return cfg


def _abs(path):
    path = os.path.expanduser(path)
    return path if os.path.isabs(path) else os.path.join(HERE, path)


# --------------------------------------------------------------------------
# Episode codes
# --------------------------------------------------------------------------

def parse_code(text):
    """Return (season, episode) found in text, or None."""
    m = CODE_RE.search(text)
    if not m:
        m = ALT_CODE_RE.search(text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def format_code(code):
    return "S%02dE%02d" % code


# --------------------------------------------------------------------------
# Library: everything playable across all media dirs
# --------------------------------------------------------------------------

class Library(object):
    def __init__(self, media_dirs, extensions, rescan_seconds):
        self.media_dirs = media_dirs
        self.extensions = extensions
        self.rescan_seconds = rescan_seconds
        self.files = []          # all playable paths
        self.by_code = {}        # (season, ep) -> path
        self.scanned_at = 0
        self.sources = {}        # dir -> file count from last scan

    def stale(self):
        return time.time() - self.scanned_at > self.rescan_seconds

    def scan(self, force=False):
        if not force and not self.stale():
            return
        files, by_code, sources = [], {}, {}
        for d in self.media_dirs:
            count = 0
            for path in self._walk(d):
                code = parse_code(os.path.basename(path))
                if code is not None:
                    if code in by_code:
                        continue  # earlier dir wins
                    by_code[code] = path
                files.append(path)
                count += 1
            sources[d] = count
        self.files, self.by_code, self.sources = files, by_code, sources
        self.scanned_at = time.time()
        log.info("library: %d files (%s)", len(files),
                 ", ".join("%s=%d" % (os.path.basename(d.rstrip("/")) or d, n)
                           for d, n in sources.items()))

    def _walk(self, root):
        if not os.path.isdir(root):
            return
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(n for n in dirnames if not n.startswith("."))
                for name in sorted(filenames):
                    if name.startswith("."):
                        continue
                    if name.lower().endswith(self.extensions):
                        yield os.path.join(dirpath, name)
        except OSError as e:
            log.warning("cannot read %s: %s", root, e)


# --------------------------------------------------------------------------
# Playlists
# --------------------------------------------------------------------------

def parse_playlist(text):
    """Parse playlist text into a list of matcher tuples."""
    rules = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = RANGE_RE.match(line)
        if m and parse_code(m.group(1)) and parse_code(m.group(2)):
            rules.append(("range", parse_code(m.group(1)), parse_code(m.group(2))))
            continue
        m = SEASON_RE.match(line)
        if m:
            rules.append(("season", int(m.group(1))))
            continue
        code = parse_code(line)
        if code and (CODE_RE.fullmatch(line) or ALT_CODE_RE.fullmatch(line)):
            rules.append(("code", code))
            continue
        rules.append(("glob", line.lower()))
    return rules


def resolve_rules(rules, library):
    """Return the list of file paths matching the rules, in rule order, deduped."""
    out, seen = [], set()

    def add(path):
        if path not in seen:
            seen.add(path)
            out.append(path)

    for rule in rules:
        kind = rule[0]
        if kind == "code":
            path = library.by_code.get(rule[1])
            if path:
                add(path)
            else:
                log.debug("playlist: %s not in library", format_code(rule[1]))
        elif kind == "season":
            for code in sorted(library.by_code):
                if code[0] == rule[1]:
                    add(library.by_code[code])
        elif kind == "range":
            lo, hi = rule[1], rule[2]
            for code in sorted(library.by_code):
                if lo <= code <= hi:
                    add(library.by_code[code])
        elif kind == "glob":
            for path in library.files:
                if fnmatch.fnmatch(os.path.basename(path).lower(), rule[1]):
                    add(path)
    return out


class Playlists(object):
    def __init__(self, playlist_dir):
        self.dir = playlist_dir

    def names(self):
        names = set(["all"])
        if os.path.isdir(self.dir):
            for n in os.listdir(self.dir):
                if n.endswith(".txt"):
                    names.add(n[:-4])
        return sorted(names)

    def exists(self, name):
        return name == "all" or os.path.exists(self.path(name))

    def path(self, name):
        return os.path.join(self.dir, name + ".txt")

    def resolve(self, name, library):
        if name == "all" and not os.path.exists(self.path("all")):
            return list(library.files)
        try:
            with open(self.path(name)) as f:
                rules = parse_playlist(f.read())
        except OSError as e:
            log.warning("playlist %s: %s", name, e)
            return []
        return resolve_rules(rules, library)


# --------------------------------------------------------------------------
# Schedule: which playlist is active today
# --------------------------------------------------------------------------

def _md(s):
    """'10-31' -> (10, 31)"""
    m, d = s.split("-")
    return int(m), int(d)


def in_window(today, start, end):
    """Inclusive month-day window; wraps across New Year if start > end."""
    t, s, e = (today.month, today.day), _md(start), _md(end)
    if s <= e:
        return s <= t <= e
    return t >= s or t <= e


class Schedule(object):
    def __init__(self, path):
        self.path = path
        self.default = "all"
        self.rules = []
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            log.warning("schedule: %s", e)
            return
        self.default = data.get("default", "all")
        self.rules = data.get("rules", [])

    def active(self, today=None):
        """Return (playlist_name, rule_dict, reason)."""
        today = today or datetime.date.today()
        for rule in self.rules:
            if in_window(today, rule["from"], rule["to"]):
                return rule["playlist"], rule, "schedule %s..%s" % (rule["from"], rule["to"])
        return self.default, {}, "schedule default"


# --------------------------------------------------------------------------
# State dir: override + now-playing, shared with tvctl
# --------------------------------------------------------------------------

class State(object):
    def __init__(self, state_dir):
        self.dir = state_dir
        os.makedirs(state_dir, exist_ok=True)

    def _p(self, name):
        return os.path.join(self.dir, name)

    def override(self):
        try:
            with open(self._p("override")) as f:
                name = f.read().strip()
            return name or None
        except OSError:
            return None

    def write(self, name, value):
        tmp = self._p(name) + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(value) + "\n")
        os.replace(tmp, self._p(name))

    def clear(self, name):
        try:
            os.remove(self._p(name))
        except OSError:
            pass


# --------------------------------------------------------------------------
# Process groups (the player child runs as its own session leader)
# --------------------------------------------------------------------------

def signal_group(pid, sig):
    """Signal the process group led by pid; fall back to the pid alone.
    Returns True if something was signalled."""
    try:
        os.killpg(pid, sig)
        return True
    except ProcessLookupError:
        pass
    except OSError:
        pass
    try:
        os.kill(pid, sig)
        return True
    except OSError:
        return False


def kill_group(pid, grace=3.0):
    """After the child exited, make sure nothing it spawned outlives it."""
    end = time.time() + grace
    while True:
        try:
            os.killpg(pid, 0)
        except OSError:
            return
        if time.time() > end:
            log.warning("player left processes behind; killing group %s", pid)
            signal_group(pid, signal.SIGKILL)
            return
        signal_group(pid, signal.SIGTERM)
        time.sleep(0.2)


# --------------------------------------------------------------------------
# Player
# --------------------------------------------------------------------------

class TV(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self.library = Library(cfg["media_dirs"], cfg["extensions"], cfg["rescan_seconds"])
        self.playlists = Playlists(cfg["playlist_dir"])
        self.schedule = Schedule(cfg["schedule_file"])
        self.state = State(cfg["state_dir"])
        self.child = None
        self.stopping = False

    def active_playlist(self):
        name = self.state.override()
        if name:
            if self.playlists.exists(name):
                return name, {}, "override"
            log.warning("override playlist %r does not exist; ignoring", name)
        return self.schedule.active()

    def pool(self, name):
        self.library.scan()
        files = self.playlists.resolve(name, self.library)
        if not files and name != "all":
            log.warning("playlist %r resolved to 0 files; falling back to 'all'", name)
            files = self.playlists.resolve("all", self.library)
        return files

    def run(self, max_plays=None):
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        plays = 0
        quick_failures = 0
        while not self.stopping:
            name, rule, reason = self.active_playlist()
            files = self.pool(name)
            if not files:
                log.warning("nothing to play (%s) - retrying in %ss",
                            ", ".join("%s=%d" % kv for kv in self.library.sources.items()),
                            self.cfg["retry_seconds"])
                self.state.write("active", "%s (no media)" % name)
                self._sleep(self.cfg["retry_seconds"])
                self.library.scan(force=True)
                continue

            mix_pool, mix_ratio = [], 0.0
            if rule.get("mix_in"):
                mix_pool = self.playlists.resolve(rule["mix_in"], self.library)
                mix_ratio = float(rule.get("mix_ratio", 0.25))

            log.info("playlist %r (%s): %d episodes", name, reason, len(files))
            self.state.write("active", "%s (%s, %d episodes)" % (name, reason, len(files)))
            queue = list(files)
            random.shuffle(queue)

            for video in queue:
                if self.stopping:
                    break
                # Re-check between episodes so date changes / tvctl take effect.
                if self.active_playlist()[0] != name:
                    log.info("playlist changed; rebuilding queue")
                    break
                if mix_pool and random.random() < mix_ratio:
                    video = random.choice(mix_pool)
                started = time.time()
                rc = self.play(video)
                plays += 1
                if rc != 0 and time.time() - started < 3:
                    quick_failures += 1
                    log.warning("player exited %s after <3s on %s", rc, video)
                    if quick_failures >= 3:
                        log.warning("repeated failures; rescanning media")
                        self._sleep(self.cfg["retry_seconds"])
                        self.library.scan(force=True)
                        quick_failures = 0
                        break
                else:
                    quick_failures = 0
                if max_plays and plays >= max_plays:
                    return
                if self.library.stale():
                    self.library.scan()
                    break  # rebuild the queue from the fresh library

    def play(self, video):
        cmd = [video if a == "{file}" else a for a in self.cfg["player_cmd"]]
        log.info("playing %s", os.path.basename(video))
        self.state.write("now_playing", video)
        try:
            # Own session/process group: /usr/bin/omxplayer is a shell wrapper
            # around omxplayer.bin, so a skip must signal the whole group or
            # the .bin keeps playing after the wrapper dies.
            self.child = subprocess.Popen(cmd, start_new_session=True)
        except OSError as e:
            log.error("cannot start player %r: %s", cmd[0], e)
            self._sleep(self.cfg["retry_seconds"])
            return 1
        self.state.write("child_pid", self.child.pid)
        rc = self.child.wait()
        kill_group(self.child.pid)
        self.child = None
        self.state.clear("child_pid")
        return rc

    def _sleep(self, seconds):
        end = time.time() + seconds
        while not self.stopping and time.time() < end:
            time.sleep(0.5)

    def _on_signal(self, signum, frame):
        self.stopping = True
        if self.child and self.child.poll() is None:
            signal_group(self.child.pid, signal.SIGTERM)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--status", action="store_true", help="show active playlist and exit")
    ap.add_argument("--list", action="store_true", help="list playlists and exit")
    ap.add_argument("--resolve", metavar="NAME", help="print files for a playlist and exit")
    ap.add_argument("--once", action="store_true", help="play one episode and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    quiet = (args.status or args.list or args.resolve) and not args.verbose
    logging.basicConfig(level=logging.DEBUG if args.verbose else (logging.WARNING if quiet else logging.INFO),
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

    tv = TV(load_config(args.config))

    if args.status:
        name, rule, reason = tv.active_playlist()
        tv.library.scan(force=True)
        files = tv.playlists.resolve(name, tv.library)
        print("active playlist: %s (%s)" % (name, reason))
        print("episodes:        %d" % len(files))
        for d, n in tv.library.sources.items():
            print("source:          %s (%d files%s)" % (d, n, "" if os.path.isdir(d) else ", missing"))
        try:
            with open(os.path.join(tv.state.dir, "now_playing")) as f:
                print("now playing:     %s" % f.read().strip())
        except OSError:
            pass
        return 0

    if args.list:
        tv.library.scan(force=True)
        name, _, _ = tv.active_playlist()
        for n in tv.playlists.names():
            files = tv.playlists.resolve(n, tv.library)
            print("%s %-16s %4d episodes" % ("*" if n == name else " ", n, len(files)))
        return 0

    if args.resolve:
        tv.library.scan(force=True)
        for path in tv.playlists.resolve(args.resolve, tv.library):
            print(path)
        return 0

    tv.run(max_plays=1 if args.once else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())

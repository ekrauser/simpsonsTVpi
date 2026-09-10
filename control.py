#!/usr/bin/env python3
"""
Control operations shared by tvctl, remote.py and anything else.

They talk to the running player through files in the state dir:
  override   forced playlist name (absent = follow the schedule)
  child_pid  pid of the omxplayer currently running (kill it = skip)

    control.py status | set NAME | auto | next | names
"""
import json
import os
import signal
import sys

import player


class Control(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self.tv = player.TV(cfg)

    # -- queries --------------------------------------------------------

    def names(self):
        return self.tv.playlists.names()

    def status(self, counts=False):
        name, rule, reason = self.tv.active_playlist()
        override = self.tv.state.override()
        out = {
            "active": name,
            "reason": reason,
            "override": override,
            "mode": "manual" if override else "auto",
            "now_playing": self._read("now_playing"),
            "playing": self._read("child_pid") is not None,
            "playlists": self.names(),
        }
        if counts:
            self.tv.library.scan(force=True)
            out["episodes"] = len(self.tv.playlists.resolve(name, self.tv.library))
            out["sources"] = self.tv.library.sources
        return out

    # -- actions --------------------------------------------------------

    def set_playlist(self, name):
        if name in ("auto", "", None):
            return self.auto()
        if not self.tv.playlists.exists(name):
            raise ValueError("no playlist named %r" % name)
        self.tv.state.write("override", name)
        self.skip()
        return name

    def auto(self):
        self.tv.state.clear("override")
        self.skip()
        return "auto"

    def skip(self):
        pid = self._read("child_pid")
        if not pid:
            return False
        try:
            return player.signal_group(int(pid), signal.SIGTERM)
        except ValueError:
            return False

    # -- helpers --------------------------------------------------------

    def _read(self, name):
        try:
            with open(os.path.join(self.tv.state.dir, name)) as f:
                return f.read().strip() or None
        except OSError:
            return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg_path = os.path.join(player.HERE, "config.json")
    if argv[:1] == ["--config"]:
        cfg_path = argv[1]
        argv = argv[2:]
    ctl = Control(player.load_config(cfg_path))
    cmd = argv[0] if argv else "status"
    try:
        if cmd == "status":
            print(json.dumps(ctl.status(counts=True), indent=2))
        elif cmd == "names":
            print("\n".join(ctl.names()))
        elif cmd == "set":
            print("override -> %s" % ctl.set_playlist(argv[1]))
        elif cmd == "auto":
            ctl.auto()
            print("override cleared; following schedule")
        elif cmd == "next":
            print("skipped" if ctl.skip() else "nothing playing")
        else:
            print(__doc__)
            return 1
    except (ValueError, IndexError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Unit + smoke tests for player.py. Run: python3 -m unittest discover tests"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import player  # noqa: E402


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("x")


class CodeTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(player.parse_code("The Simpsons - S05E05 - THOH IV.mp4"), (5, 5))
        self.assertEqual(player.parse_code("simpsons.s12e01.720p.mkv"), (12, 1))
        self.assertEqual(player.parse_code("The Simpsons 5x05.mp4"), (5, 5))
        self.assertEqual(player.parse_code("S5E5"), (5, 5))
        self.assertIsNone(player.parse_code("random clip.mp4"))
        self.assertIsNone(player.parse_code("1080p.mp4"))

    def test_playlist_parse(self):
        rules = player.parse_playlist("""
            # comment
            S05E05   # THOH IV
            s03
            S01E01 - S02E22
            *treehouse*
            5x06
        """)
        self.assertEqual(rules, [
            ("code", (5, 5)),
            ("season", 3),
            ("range", (1, 1), (2, 22)),
            ("glob", "*treehouse*"),
            ("code", (5, 6)),
        ])


class ScheduleTests(unittest.TestCase):
    def test_window(self):
        d = datetime.date
        self.assertTrue(player.in_window(d(2026, 10, 15), "10-01", "10-31"))
        self.assertTrue(player.in_window(d(2026, 10, 1), "10-01", "10-31"))
        self.assertTrue(player.in_window(d(2026, 10, 31), "10-01", "10-31"))
        self.assertFalse(player.in_window(d(2026, 11, 1), "10-01", "10-31"))
        # wraps New Year
        self.assertTrue(player.in_window(d(2026, 12, 25), "12-20", "01-02"))
        self.assertTrue(player.in_window(d(2027, 1, 1), "12-20", "01-02"))
        self.assertFalse(player.in_window(d(2026, 6, 1), "12-20", "01-02"))

    def test_shipped_schedule(self):
        sched = player.Schedule(os.path.join(os.path.dirname(__file__), "..", "playlists", "schedule.json"))
        self.assertEqual(sched.active(datetime.date(2026, 10, 31))[0], "halloween")
        self.assertEqual(sched.active(datetime.date(2026, 12, 24))[0], "christmas")
        self.assertEqual(sched.active(datetime.date(2026, 11, 26))[0], "thanksgiving")
        self.assertEqual(sched.active(datetime.date(2026, 7, 4))[0], "all")


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.local = os.path.join(self.tmp, "videos")
        self.net = os.path.join(self.tmp, "mnt")
        touch(os.path.join(self.local, "The Simpsons - S01E01 - Roasting.mp4"))
        touch(os.path.join(self.net, "Season 01", "The Simpsons - S01E01 - Roasting.mp4"))
        touch(os.path.join(self.net, "Season 05", "The Simpsons - S05E05 - Treehouse of Horror IV.mp4"))
        touch(os.path.join(self.net, "Season 07", "The Simpsons - S07E11 - Marge Be Not Proud.mp4"))
        touch(os.path.join(self.net, "extras", "clip.mp4"))
        touch(os.path.join(self.net, "extras", "notes.txt"))
        touch(os.path.join(self.net, ".hidden", "S09E09.mp4"))
        self.lib = player.Library([self.local, self.net], (".mp4",), 0)
        self.lib.scan(force=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_scan_prefers_first_dir_and_skips_junk(self):
        self.assertEqual(len(self.lib.files), 4)
        self.assertTrue(self.lib.by_code[(1, 1)].startswith(self.local))
        self.assertNotIn((9, 9), self.lib.by_code)

    def test_missing_dir_is_fine(self):
        lib = player.Library([os.path.join(self.tmp, "nope"), self.net], (".mp4",), 0)
        lib.scan(force=True)
        self.assertEqual(lib.sources[os.path.join(self.tmp, "nope")], 0)
        self.assertEqual(len(lib.files), 4)

    def test_resolve(self):
        r = player.resolve_rules(player.parse_playlist("S05E05\nS99E99\n*clip*"), self.lib)
        self.assertEqual([os.path.basename(p) for p in r],
                         ["The Simpsons - S05E05 - Treehouse of Horror IV.mp4", "clip.mp4"])
        r = player.resolve_rules(player.parse_playlist("S01E01 - S06E01"), self.lib)
        self.assertEqual(len(r), 2)
        r = player.resolve_rules(player.parse_playlist("S07"), self.lib)
        self.assertEqual(len(r), 1)


class SmokeTests(unittest.TestCase):
    """Run the real loop with a fake player binary."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.media = os.path.join(self.tmp, "media")
        for name in ["S01E01.mp4", "S05E05 - Treehouse of Horror IV.mp4", "S07E11 - Marge Be Not Proud.mp4"]:
            touch(os.path.join(self.media, name))
        pl = os.path.join(self.tmp, "playlists")
        os.makedirs(pl)
        with open(os.path.join(pl, "halloween.txt"), "w") as f:
            f.write("S05E05\n")
        with open(os.path.join(pl, "schedule.json"), "w") as f:
            json.dump({"default": "all", "rules": [{"playlist": "halloween", "from": "10-01", "to": "10-31"}]}, f)
        self.log = os.path.join(self.tmp, "played.log")
        fake = os.path.join(self.tmp, "fakeplayer")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\necho \"$3\" >> '%s'\n" % self.log)
        os.chmod(fake, 0o755)
        self.cfg = {
            "media_dirs": [self.media],
            "playlist_dir": pl,
            "schedule_file": os.path.join(pl, "schedule.json"),
            "state_dir": os.path.join(self.tmp, "state"),
            "player_cmd": [fake, "--flag", "x", "{file}"],
            "retry_seconds": 0,
        }
        self.cfgfile = os.path.join(self.tmp, "config.json")
        with open(self.cfgfile, "w") as f:
            json.dump(self.cfg, f)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def played(self):
        with open(self.log) as f:
            return [os.path.basename(l.strip()) for l in f]

    def test_plays_from_active_playlist(self):
        tv = player.TV(player.load_config(self.cfgfile))
        tv.state.write("override", "halloween")
        tv.run(max_plays=3)
        self.assertEqual(self.played(), ["S05E05 - Treehouse of Horror IV.mp4"] * 3)
        with open(os.path.join(self.cfg["state_dir"], "now_playing")) as f:
            self.assertIn("S05E05", f.read())

    def test_plays_everything_by_default(self):
        tv = player.TV(player.load_config(self.cfgfile))
        tv.schedule.rules = []  # pretend it's not October
        tv.run(max_plays=6)
        self.assertEqual(sorted(set(self.played())),
                         ["S01E01.mp4", "S05E05 - Treehouse of Horror IV.mp4", "S07E11 - Marge Be Not Proud.mp4"])

    def test_falls_back_to_all_when_playlist_empty(self):
        os.remove(os.path.join(self.media, "S05E05 - Treehouse of Horror IV.mp4"))
        tv = player.TV(player.load_config(self.cfgfile))
        tv.state.write("override", "halloween")
        tv.run(max_plays=2)
        self.assertEqual(len(self.played()), 2)

    def test_cli_status_and_list(self):
        import io
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            player.main(["--config", self.cfgfile, "--list"])
        self.assertIn("halloween", out.getvalue())
        self.assertIn("all", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            player.main(["--config", self.cfgfile, "--resolve", "halloween"])
        self.assertIn("S05E05", out.getvalue())


if __name__ == "__main__":
    unittest.main()

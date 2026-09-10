#!/usr/bin/env python3
"""Tests for control.py and remote.py (HTTP API + HA discovery payloads)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import player  # noqa: E402
import remote  # noqa: E402
from control import Control  # noqa: E402


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("x")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        media = os.path.join(self.tmp, "media")
        touch(os.path.join(media, "S05E05.mp4"))
        touch(os.path.join(media, "S01E01.mp4"))
        pl = os.path.join(self.tmp, "playlists")
        os.makedirs(pl)
        with open(os.path.join(pl, "halloween.txt"), "w") as f:
            f.write("S05E05\n")
        with open(os.path.join(pl, "schedule.json"), "w") as f:
            json.dump({"default": "all", "rules": []}, f)
        self.cfg = player.load_config(None)
        self.cfg.update({
            "media_dirs": [media], "playlist_dir": pl,
            "schedule_file": os.path.join(pl, "schedule.json"),
            "state_dir": os.path.join(self.tmp, "state"),
        })
        self.ctl = Control(self.cfg)

    def tearDown(self):
        shutil.rmtree(self.tmp)


class ControlTests(Base):
    def test_status_and_set(self):
        st = self.ctl.status()
        self.assertEqual(st["active"], "all")
        self.assertEqual(st["mode"], "auto")
        self.assertEqual(st["playlists"], ["all", "halloween"])
        self.ctl.set_playlist("halloween")
        st = self.ctl.status(counts=True)
        self.assertEqual((st["active"], st["mode"], st["episodes"]), ("halloween", "manual", 1))
        self.ctl.set_playlist("auto")
        self.assertEqual(self.ctl.status()["mode"], "auto")
        with self.assertRaises(ValueError):
            self.ctl.set_playlist("bogus")

    def test_skip_kills_child(self):
        import subprocess
        child = subprocess.Popen(["sleep", "30"])
        self.ctl.tv.state.write("child_pid", child.pid)
        self.assertTrue(self.ctl.skip())
        self.assertNotEqual(child.wait(timeout=5), 0)
        self.ctl.tv.state.clear("child_pid")
        self.assertFalse(self.ctl.skip())


class HttpTests(Base):
    def setUp(self):
        super().setUp()
        self.srv = remote.serve_http(self.ctl, 0, host="127.0.0.1")
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def req(self, path, method="GET"):
        r = urllib.request.Request(self.url + path, method=method)
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_api(self):
        code, body = self.req("/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["active"], "all")

        code, body = self.req("/api/playlist/halloween", "POST")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["override"], "halloween")
        self.assertEqual(json.loads(self.req("/api/status")[1])["active"], "halloween")

        code, body = self.req("/api/playlist/bogus", "POST")
        self.assertEqual(code, 404)

        code, body = self.req("/api/auto", "POST")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(self.req("/api/status")[1])["mode"], "auto")

        code, body = self.req("/api/next", "POST")
        self.assertEqual(json.loads(body), {"ok": True, "skipped": False})

        code, body = self.req("/api/playlists")
        self.assertEqual(json.loads(body), ["all", "halloween"])

        code, body = self.req("/")
        self.assertEqual(code, 200)
        self.assertIn("<option value=\"halloween\">", body)

        self.assertEqual(self.req("/nope")[0], 404)


class DiscoveryTests(Base):
    def test_payloads(self):
        mq = remote.remote_config({})["mqtt"]
        msgs = dict(remote.discovery_messages(mq, ["all", "halloween"]))
        sel = msgs["homeassistant/select/simpsonstv/playlist/config"]
        self.assertEqual(sel["options"], ["auto", "all", "halloween"])
        self.assertEqual(sel["command_topic"], "simpsonstv/playlist/set")
        self.assertEqual(sel["device"]["identifiers"], ["simpsonstv"])
        self.assertIn("homeassistant/button/simpsonstv/next/config", msgs)
        self.assertIn("homeassistant/sensor/simpsonstv/now_playing/config", msgs)
        for cfg in msgs.values():
            json.dumps(cfg)  # must be serialisable
            self.assertEqual(cfg["availability_topic"], "simpsonstv/availability")

    def test_state_messages(self):
        mq = remote.remote_config({"remote": {"mqtt": {"prefix": "tv"}}})["mqtt"]
        self.ctl.tv.state.write("now_playing", "/x/The Simpsons - S05E05.mp4")
        msgs = dict(remote.state_messages(mq, self.ctl.status()))
        self.assertEqual(msgs["tv/playlist"], "auto")
        self.assertEqual(msgs["tv/active"], "all")
        self.assertEqual(msgs["tv/now_playing"], "The Simpsons - S05E05")

    def test_remote_config_merge(self):
        rc = remote.remote_config({"remote": {"http_port": 9000, "mqtt": {"host": "ha.local"}}})
        self.assertEqual(rc["http_port"], 9000)
        self.assertEqual(rc["mqtt"]["host"], "ha.local")
        self.assertEqual(rc["mqtt"]["port"], 1883)


if __name__ == "__main__":
    unittest.main()

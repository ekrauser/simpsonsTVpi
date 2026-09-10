#!/usr/bin/env python3
"""
Remote control for the Simpsons TV: HTTP API + Home Assistant over MQTT.

HTTP (always on, LAN only, no auth):
    GET  /api/status            JSON: active playlist, now playing, playlists
    POST /api/next              skip the current episode
    POST /api/playlist/NAME     force a playlist ("auto" = follow schedule)
    POST /api/auto              same as /api/playlist/auto
    GET  /                      tiny web page with the same controls
  (GET also works for the actions, so a phone browser bookmark can skip.)

MQTT (when config.json has remote.mqtt.host):
  Publishes Home Assistant discovery messages, so a "Simpsons TV" device
  appears with a Playlist select, a Next button and Now Playing / Active
  Playlist sensors. Topics under <prefix>/ (default simpsonstv):
    playlist/set   <- select command ("auto" or a playlist name)
    playlist       -> select state
    next           <- button press
    now_playing    -> sensor
    active         -> sensor (what's actually playing incl. schedule)
    availability   -> online/offline (last will)
"""
import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

import player
from control import Control

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None

log = logging.getLogger("tvremote")

DEFAULT_REMOTE = {
    "http_port": 8080,
    "mqtt": {
        "host": "",
        "port": 1883,
        "username": "",
        "password": "",
        "prefix": "simpsonstv",
        "discovery_prefix": "homeassistant",
        "device_name": "Simpsons TV",
    },
}

PAGE = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>Simpsons TV</title>
<style>body{font-family:sans-serif;max-width:420px;margin:2em auto;padding:0 1em}
button,select{font-size:1.2em;padding:.5em 1em;margin:.3em 0;width:100%%}
pre{background:#eee;padding:.5em;white-space:pre-wrap}</style>
<h1>Simpsons TV</h1>
<pre id=s>loading...</pre>
<button onclick="go('/api/next')">Next episode</button>
<select id=p onchange="go('/api/playlist/'+this.value)">%s</select>
<script>
function go(u){fetch(u,{method:'POST'}).then(refresh)}
function refresh(){fetch('/api/status').then(r=>r.json()).then(j=>{
 document.getElementById('s').textContent='playlist: '+j.active+' ('+j.reason+')\\nnow: '+(j.now_playing||'-').split('/').pop();
 document.getElementById('p').value=j.override||'auto'})}
refresh();setInterval(refresh,5000)</script>"""


def remote_config(cfg):
    r = json.loads(json.dumps(DEFAULT_REMOTE))
    user = cfg.get("remote", {})
    r.update({k: v for k, v in user.items() if k != "mqtt"})
    r["mqtt"].update(user.get("mqtt", {}))
    return r


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def make_handler(ctl):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.debug("http %s " + fmt, self.client_address[0], *args)

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _route(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/":
                opts = "".join('<option value="%s">%s</option>' % (n, n) for n in ["auto"] + ctl.names())
                return self._send(200, PAGE % opts, "text/html")
            if path == "/api/status":
                return self._send(200, ctl.status())
            if path == "/api/next":
                return self._send(200, {"ok": True, "skipped": ctl.skip()})
            if path == "/api/auto":
                ctl.auto()
                return self._send(200, {"ok": True, "override": None, "mode": "auto"})
            if path.startswith("/api/playlist/"):
                name = path[len("/api/playlist/"):]
                try:
                    ctl.set_playlist(name)
                except ValueError as e:
                    return self._send(404, {"ok": False, "error": str(e)})
                return self._send(200, {"ok": True, "override": ctl.tv.state.override()})
            if path == "/api/playlists":
                return self._send(200, ctl.names())
            self._send(404, {"ok": False, "error": "not found"})

        def do_GET(self):
            self._route()

        def do_POST(self):
            self._route()

        do_HEAD = do_GET

    return Handler


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve_http(ctl, port, host="0.0.0.0"):
    srv = Server((host, port), make_handler(ctl))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    log.info("http: listening on %s:%d", host, port)
    return srv


# --------------------------------------------------------------------------
# MQTT / Home Assistant
# --------------------------------------------------------------------------

def discovery_messages(mq, names):
    """Return [(topic, payload_dict)] for HA MQTT discovery."""
    p, d = mq["prefix"], mq["discovery_prefix"]
    device = {"identifiers": [p], "name": mq["device_name"],
              "manufacturer": "withrow.io build", "model": "Raspberry Pi Zero"}
    base = {"availability_topic": p + "/availability", "device": device}

    def ent(kind, key, extra):
        cfg = dict(base)
        cfg.update({"unique_id": "%s_%s" % (p, key), "object_id": "%s_%s" % (p, key)})
        cfg.update(extra)
        return ("%s/%s/%s/%s/config" % (d, kind, p, key), cfg)

    return [
        ent("select", "playlist", {"name": "Playlist", "icon": "mdi:playlist-play",
                                   "command_topic": p + "/playlist/set", "state_topic": p + "/playlist",
                                   "options": ["auto"] + names}),
        ent("button", "next", {"name": "Next episode", "icon": "mdi:skip-next",
                               "command_topic": p + "/next", "payload_press": "PRESS"}),
        ent("sensor", "now_playing", {"name": "Now playing", "icon": "mdi:television-classic",
                                      "state_topic": p + "/now_playing"}),
        ent("sensor", "active", {"name": "Active playlist", "icon": "mdi:calendar-star",
                                 "state_topic": p + "/active"}),
    ]


def state_messages(mq, status):
    p = mq["prefix"]
    now = status["now_playing"] or "nothing"
    return [
        (p + "/playlist", status["override"] or "auto"),
        (p + "/active", status["active"]),
        (p + "/now_playing", os.path.splitext(os.path.basename(now))[0]),
    ]


class Bridge(object):
    def __init__(self, ctl, mq):
        self.ctl, self.mq = ctl, mq
        self.prefix = mq["prefix"]
        self.last = None
        self.client = mqtt.Client(client_id=self.prefix + "-" + socket.gethostname())
        if mq.get("username"):
            self.client.username_pw_set(mq["username"], mq.get("password") or None)
        self.client.will_set(self.prefix + "/availability", "offline", retain=True)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.reconnect_delay_set(2, 60)

    def start(self):
        log.info("mqtt: connecting to %s:%s", self.mq["host"], self.mq["port"])
        self.client.connect_async(self.mq["host"], int(self.mq["port"]), 60)
        self.client.loop_start()

    def stop(self):
        self.client.publish(self.prefix + "/availability", "offline", retain=True)
        self.client.loop_stop()
        self.client.disconnect()

    def on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            log.warning("mqtt: connect failed rc=%s", rc)
            return
        log.info("mqtt: connected")
        client.subscribe([(self.prefix + "/playlist/set", 0), (self.prefix + "/next", 0)])
        self.announce()
        client.publish(self.prefix + "/availability", "online", retain=True)
        self.publish_state(force=True)

    def on_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="replace").strip()
        log.info("mqtt: %s = %s", msg.topic, payload)
        try:
            if msg.topic == self.prefix + "/next":
                self.ctl.skip()
            elif msg.topic == self.prefix + "/playlist/set":
                self.ctl.set_playlist(payload)
        except ValueError as e:
            log.warning("mqtt: %s", e)
        self.publish_state(force=True)

    def announce(self):
        self.names = self.ctl.names()
        for topic, cfg in discovery_messages(self.mq, self.names):
            self.client.publish(topic, json.dumps(cfg), retain=True)

    def publish_state(self, force=False):
        status = self.ctl.status()
        if status["playlists"] != getattr(self, "names", None):
            self.announce()
        msgs = state_messages(self.mq, status)
        if force or msgs != self.last:
            for topic, payload in msgs:
                self.client.publish(topic, payload, retain=True)
            self.last = msgs


# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(player.HERE, "config.json"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

    cfg = player.load_config(args.config)
    rc = remote_config(cfg)
    ctl = Control(cfg)

    serve_http(ctl, int(rc["http_port"]))

    bridge = None
    if rc["mqtt"].get("host"):
        if mqtt is None:
            log.error("mqtt: host configured but paho-mqtt is not installed (apt install python3-paho-mqtt)")
        else:
            bridge = Bridge(ctl, rc["mqtt"])
            bridge.start()
    else:
        log.info("mqtt: not configured (set remote.mqtt.host in config.json)")

    try:
        while True:
            time.sleep(2)
            if bridge:
                bridge.publish_state()
    except KeyboardInterrupt:
        pass
    finally:
        if bridge:
            bridge.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Turn a Plex playlist into a Simpsons TV playlist file.

Build the playlist in Plex (drag episodes into a playlist named e.g.
"Simpsons - Christmas"), then:

    export PLEX_URL=http://plex.local:32400
    export PLEX_TOKEN=xxxxxxxxxxxxxxxxxxxx

    python3 plex_export.py list
    python3 plex_export.py export "Simpsons - Christmas" > ../playlists/christmas.txt

Commit the file, run update.sh (or wait for the nightly timer) on the Pi.

Finding your token: https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/
No dependencies beyond the standard library.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


class Plex(object):
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    def get(self, path, **params):
        params["X-Plex-Token"] = self.token
        full = "%s%s?%s" % (self.url, path, urllib.parse.urlencode(params))
        req = urllib.request.Request(full, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp).get("MediaContainer", {})
        except urllib.error.HTTPError as e:
            sys.exit("Plex returned HTTP %s for %s (bad token?)" % (e.code, path))
        except urllib.error.URLError as e:
            sys.exit("cannot reach Plex at %s: %s" % (self.url, e.reason))

    def playlists(self):
        return [p for p in self.get("/playlists").get("Metadata", [])
                if p.get("playlistType") == "video"]

    def playlist_items(self, rating_key):
        return self.get("/playlists/%s/items" % rating_key).get("Metadata", [])

    def find_playlist(self, name):
        for p in self.playlists():
            if p["title"].lower() == name.lower():
                return p
        return None


def episode_line(item):
    season, ep = item.get("parentIndex"), item.get("index")
    if season is None or ep is None:
        return None
    title = item.get("title", "")
    show = item.get("grandparentTitle", "")
    return "S%02dE%02d   # %s" % (int(season), int(ep), title), show


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("PLEX_URL", "http://localhost:32400"))
    ap.add_argument("--token", default=os.environ.get("PLEX_TOKEN"))
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="list video playlists on the server")
    ex = sub.add_parser("export", help="print a playlist as episode codes")
    ex.add_argument("name", help="Plex playlist title (case-insensitive)")
    ex.add_argument("--show", help="only keep episodes of this show (default: all)")
    args = ap.parse_args()

    if not args.cmd:
        ap.print_help()
        return 1
    if not args.token:
        sys.exit("set PLEX_TOKEN or pass --token")

    plex = Plex(args.url, args.token)

    if args.cmd == "list":
        for p in plex.playlists():
            print("%-40s %4s items" % (p["title"], p.get("leafCount", "?")))
        return 0

    pl = plex.find_playlist(args.name)
    if not pl:
        sys.exit("no video playlist named %r (try: plex_export.py list)" % args.name)

    print("# Exported from Plex playlist %r" % pl["title"])
    print("# Regenerate with: plex_export.py export %s" % json.dumps(pl["title"]))
    print()
    kept = skipped = 0
    for item in plex.playlist_items(pl["ratingKey"]):
        if item.get("type") != "episode":
            skipped += 1
            continue
        res = episode_line(item)
        if not res:
            skipped += 1
            continue
        line, show = res
        if args.show and show.lower() != args.show.lower():
            skipped += 1
            continue
        print(line)
        kept += 1
    if skipped:
        print("# (%d non-episode/other-show items skipped)" % skipped)
    print("# %d episodes" % kept, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

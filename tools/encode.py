#!/usr/bin/env python3
"""
Transcode a TV library into Pi-Zero-friendly files.

Run this on a real computer (your desktop or the NAS), never on the Pi.
Point it at the folder Plex already indexes and at the share the Pi mounts:

    python3 encode.py --src "/volume1/video/TV/The Simpsons" --dst /volume1/media/simpsonstv

It walks --src recursively, skips anything already in --dst, and keeps the
source filename (so S05E05 codes survive for playlists). Output is 480p
H.264 baseline + AAC, which the Pi Zero decodes in hardware, with
+faststart so it streams cleanly over SMB/NFS.

Requires ffmpeg on PATH (or --ffmpeg /path/to/ffmpeg).
"""
import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys

VIDEO_EXT = (".mp4", ".mkv", ".mov", ".avi", ".m4v", ".ts", ".wmv")


def find_sources(src):
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.lower().endswith(VIDEO_EXT) and not name.startswith("."):
                yield os.path.join(dirpath, name)


def build_cmd(ffmpeg, src, dst, height, crf, preset):
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", src,
        "-map", "0:v:0", "-map", "0:a:0", "-sn", "-dn",
        "-vf", "scale=-2:%d" % height,
        "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
        "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2",
        "-movflags", "+faststart",
        dst,
    ]


def encode_one(ffmpeg, src, dst, height, crf, preset, dry_run):
    tmp = dst + ".part.mp4"
    if dry_run:
        return src, dst, "would encode"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        subprocess.run(build_cmd(ffmpeg, src, tmp, height, crf, preset), check=True)
        os.replace(tmp, dst)
        return src, dst, "ok"
    except subprocess.CalledProcessError as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        return src, dst, "FAILED (ffmpeg exit %d)" % e.returncode
    except KeyboardInterrupt:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="folder with the full-quality episodes (Plex library)")
    ap.add_argument("--dst", required=True, help="folder the Pi mounts; encoded files land here")
    ap.add_argument("--flat", action="store_true", help="don't mirror season sub-folders in --dst")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--crf", type=int, default=23)
    ap.add_argument("--preset", default="fast")
    ap.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(args.src):
        sys.exit("--src is not a directory: %s" % args.src)
    if not args.dry_run and not shutil.which(args.ffmpeg):
        sys.exit("ffmpeg not found (%s); install it or pass --ffmpeg" % args.ffmpeg)

    jobs = []
    for src in find_sources(os.path.abspath(args.src)):
        rel = os.path.relpath(src, os.path.abspath(args.src))
        if args.flat:
            rel = os.path.basename(rel)
        dst = os.path.join(os.path.abspath(args.dst), os.path.splitext(rel)[0] + ".mp4")
        if os.path.exists(dst):
            continue
        jobs.append((src, dst))

    print("%d file(s) to encode with %d worker(s)" % (len(jobs), args.jobs))
    failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(encode_one, args.ffmpeg, s, d, args.height, args.crf, args.preset, args.dry_run)
                   for s, d in jobs]
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            src, dst, status = fut.result()
            if status.startswith("FAILED"):
                failed += 1
            print("[%d/%d] %s -> %s: %s" % (i, len(jobs), os.path.basename(src), os.path.basename(dst), status))
    if failed:
        sys.exit("%d file(s) failed" % failed)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Removes songs you keep skipping from chosen Spotify playlists.

Usage:
    python skip_pruner.py --auth     # one-time login (do this on a machine with a browser)
    python skip_pruner.py            # run the watcher
"""
import argparse
import json
import logging
import os
import sys
import time
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

BASE = Path(__file__).resolve().parent
SKIPS_FILE = BASE / "skips.json"
CACHE_FILE = BASE / ".spotify_cache"
SCOPE = "user-read-playback-state playlist-read-private playlist-modify-private playlist-modify-public"
FINISHED_MARGIN_MS = 6000   # leaving a song with under 6s left counts as finished, not skipped
HOUSEKEEPING_EVERY = 24 * 3600

log = logging.getLogger("skip-pruner")


def utcnow():
    return datetime.now(timezone.utc)


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_config(path):
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_playlists"] = {p["id"]: p for p in cfg.get("playlists", [])}
    return cfg


def make_client(cfg):
    s = cfg["spotify"]
    auth = SpotifyOAuth(
        client_id=s["client_id"],
        client_secret=s["client_secret"],
        redirect_uri=s["redirect_uri"],
        scope=SCOPE,
        cache_path=str(CACHE_FILE),
    )
    # retries=0: we handle 429 ourselves so we can obey Retry-After
    return spotipy.Spotify(auth_manager=auth, requests_timeout=10, retries=0, status_retries=0)


# ---------- skip storage ----------
# skips.json layout: {playlist_id: {track_uri: {"name": str, "skips": [iso timestamps]}}}

def load_skips():
    if SKIPS_FILE.exists():
        try:
            return json.loads(SKIPS_FILE.read_text())
        except json.JSONDecodeError:
            log.warning("skips.json was corrupt, starting fresh")
    return {}


def save_skips(data):
    tmp = SKIPS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, SKIPS_FILE)


def pl_setting(cfg, pl, key):
    return pl.get(key, cfg["settings"].get("default_" + key))


# ---------- housekeeping ----------

def prune_old_skips(cfg, skips):
    changed = False
    for pl_id in list(skips):
        pl = cfg["_playlists"].get(pl_id)
        if not pl:
            continue
        cutoff = utcnow() - timedelta(days=pl_setting(cfg, pl, "window_days"))
        for uri in list(skips[pl_id]):
            kept = [t for t in skips[pl_id][uri]["skips"] if parse_ts(t) > cutoff]
            if len(kept) != len(skips[pl_id][uri]["skips"]):
                changed = True
            if kept:
                skips[pl_id][uri]["skips"] = kept
            else:
                del skips[pl_id][uri]
    if changed:
        save_skips(skips)


def purge_archive(cfg, sp):
    arch = cfg.get("archive", {})
    if not arch.get("enabled"):
        return
    cutoff = utcnow() - timedelta(days=arch["retention_days"])
    newest = {}  # uri -> newest added_at, so a re-archived song is not deleted early
    res = sp.playlist_items(arch["playlist_id"], limit=100,
                            fields="items(added_at,track(uri)),next")
    while res:
        for it in res["items"]:
            if it.get("track") and it.get("added_at"):
                t = parse_ts(it["added_at"])
                u = it["track"]["uri"]
                newest[u] = max(newest.get(u, t), t)
        res = sp.next(res) if res.get("next") else None
    old = [u for u, t in newest.items() if t < cutoff]
    for i in range(0, len(old), 100):
        chunk = old[i:i + 100]
        if cfg["settings"].get("dry_run"):
            log.info("[dry run] would purge %d old songs from archive", len(chunk))
        else:
            sp.playlist_remove_all_occurrences_of_items(arch["playlist_id"], chunk)
            log.info("purged %d old songs from archive", len(chunk))


def housekeeping(cfg, sp, skips):
    prune_old_skips(cfg, skips)
    purge_archive(cfg, sp)


# ---------- core logic ----------

def record_skip(cfg, sp, skips, pl, uri, name):
    pl_id = pl["id"]
    entry = skips.setdefault(pl_id, {}).setdefault(uri, {"name": name, "skips": []})
    entry["skips"].append(utcnow().isoformat())
    cutoff = utcnow() - timedelta(days=pl_setting(cfg, pl, "window_days"))
    entry["skips"] = [t for t in entry["skips"] if parse_ts(t) > cutoff]
    count, limit = len(entry["skips"]), pl["skips_before_removal"]
    log.info("skip %d/%d in '%s': %s", count, limit, pl["name"], name)

    if count >= limit:
        if cfg["settings"].get("dry_run"):
            log.info("[dry run] would remove from '%s': %s", pl["name"], name)
        else:
            arch = cfg.get("archive", {})
            if arch.get("enabled"):
                sp.playlist_add_items(arch["playlist_id"], [uri])  # archive first, so removal is never permanent
            sp.playlist_remove_all_occurrences_of_items(pl_id, [uri])
            log.info("REMOVED from '%s': %s", pl["name"], name)
            del skips[pl_id][uri]
    save_skips(skips)


def evaluate_transition(cfg, sp, skips, cur, new_uri, new_pl_id, now):
    """Called when the track changed. Decide whether the old track was skipped."""
    s = cfg["settings"]
    max_gap = max(4 * s["poll_interval_seconds"], 15)
    if now - cur["last_seen"] > max_gap:
        return  # stale data (paused, offline, lost signal): ignore
    pl = cfg["_playlists"].get(cur["pl_id"])
    if not pl or new_pl_id != cur["pl_id"]:
        return  # not a tracked playlist, or the playlist context changed
    if new_uri == cur["prev_uri"] and not s.get("count_back_button", False):
        return  # back button, not counted
    threshold = pl_setting(cfg, pl, "played_threshold_percent") / 100
    remaining = cur["duration"] - cur["progress"]
    if cur["progress"] >= threshold * cur["duration"] or remaining <= FINISHED_MARGIN_MS:
        return  # played through
    record_skip(cfg, sp, skips, pl, cur["uri"], cur["name"])


def tick(cfg, sp, skips, state):
    s = cfg["settings"]
    pb = sp.current_playback()
    item = pb.get("item") if pb else None
    if not pb or not item or item.get("type") != "track":
        return s["idle_poll_interval_seconds"]
    if not pb.get("is_playing"):
        return s["poll_interval_seconds"]  # paused: keep state, do not refresh last_seen

    now = time.time()
    ctx = pb.get("context")
    pl_id = ctx["uri"].split(":")[-1] if ctx and ctx.get("type") == "playlist" else None
    uri = item["uri"]
    name = f'{item["artists"][0]["name"]} - {item["name"]}' if item.get("artists") else item["name"]
    cur = state.get("cur")

    if cur and cur["uri"] == uri:
        cur.update(progress=pb.get("progress_ms") or 0, last_seen=now, pl_id=pl_id)
        return s["poll_interval_seconds"]

    if cur:
        evaluate_transition(cfg, sp, skips, cur, uri, pl_id, now)

    state["cur"] = {
        "uri": uri, "name": name, "pl_id": pl_id,
        "duration": item["duration_ms"], "progress": pb.get("progress_ms") or 0,
        "last_seen": now, "prev_uri": cur["uri"] if cur else None,
    }
    return s["poll_interval_seconds"]


def run(cfg):
    sp = make_client(cfg)
    skips = load_skips()
    state = {}
    last_housekeeping = 0.0
    interval = cfg["settings"]["poll_interval_seconds"]
    log.info("started, watching %d playlist(s), dry_run=%s",
             len(cfg["_playlists"]), cfg["settings"].get("dry_run"))

    while True:
        try:
            if time.time() - last_housekeeping > HOUSEKEEPING_EVERY:
                housekeeping(cfg, sp, skips)
                last_housekeeping = time.time()
            interval = tick(cfg, sp, skips, state)
        except SpotifyException as e:
            if e.http_status == 429:
                wait = int((e.headers or {}).get("Retry-After", 30)) + 1
                log.warning("rate limited, waiting %ds", wait)
                interval = wait
            else:
                log.error("Spotify error: %s", e)
                interval = 15
        except Exception as e:  # network blips etc: log and keep going
            log.error("error: %s", e)
            interval = 15
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(BASE / "config.toml"))
    ap.add_argument("--auth", action="store_true", help="one-time login, writes .spotify_cache")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(BASE / "skip_pruner.log")])
    cfg = load_config(args.config)

    if args.auth:
        sp = make_client(cfg)
        print("Logged in as", sp.current_user()["display_name"])
        return
    run(cfg)


if __name__ == "__main__":
    main()

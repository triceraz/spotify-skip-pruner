# Spotify skip pruner

A small script that watches what you play on Spotify and removes songs you keep skipping from playlists you choose.

It polls the Spotify Web API (which reflects playback on any of your devices), and if you leave a song from a configured playlist before a set percentage has played, it counts one skip. When a song reaches your skip limit within a time window, it is copied to an archive playlist and removed from the source playlist. Archived songs are deleted after a retention period.

## Features

- Per-playlist settings: skips before removal, played-through threshold, time window
- Only counts skips while playing from the playlist itself (playlist context is checked)
- Back button presses are not counted by default
- Skips expire after a configurable number of days
- Removed songs go to an archive playlist first, so nothing is lost permanently
- Dry run mode that only logs what would be removed
- Handles Spotify rate limiting (HTTP 429, obeys Retry-After)
- Tolerates missed polls: it ignores odd transitions rather than guessing, so it may undercount skips but should not overcount them

## Requirements

- Python 3.11 or newer
- A Spotify account and a free app from the [Spotify developer dashboard](https://developer.spotify.com/dashboard)
- `pip install spotipy`

## Setup

1. Create a Spotify app. Add `http://127.0.0.1:8888/callback` as a redirect URI.
2. Copy `config.example.toml` to `config.toml` and fill in your client ID, client secret, archive playlist ID and the playlists to prune.
3. Log in once on a machine with a browser: `python skip_pruner.py --auth`. This creates `.spotify_cache`.
4. Run it: `python skip_pruner.py`

Keep `dry_run = true` until the log shows what you expect, then set it to `false` and restart.

## Running on a server

Do step 3 on your laptop, then copy `skip_pruner.py`, `config.toml` and `.spotify_cache` to the server. Run it as a systemd service:

```ini
[Unit]
Description=Spotify skip pruner
After=network-online.target

[Service]
User=USER
WorkingDirectory=/home/USER/spotify-skip-pruner
ExecStart=/home/USER/spotify-skip-pruner/venv/bin/python skip_pruner.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

The config is only read at startup, so restart the service after editing it.

## Notes

- Songs only count if you started playback from the playlist page. Playing from search, the queue or Liked Songs has no playlist context and is ignored.
- Never commit `config.toml` or `.spotify_cache`. Both are in `.gitignore`.
- Spotify changes its API access rules from time to time. If calls start failing, update spotipy and check Spotify's current docs.

# Spotify skip pruner

A small script that watches what you play on Spotify and removes songs you keep skipping from playlists you choose.
NB: NEEDS TO RUN ON YOUR OWN COMPUTER / SERVER

It polls the Spotify Web API (which reflects playback on any of your devices), and if you leave a song from a configured playlist before a set percentage has played, it counts one skip. When a song reaches your skip limit within a time window, it is copied to an archive playlist and removed from the source playlist. Archived songs are deleted after a retention period.

## Features

- Per-playlist: Configure each playlist you want to be tracked and how you want to track them
- Only counts songs when playing from the correct playlist (keep context)
- Checks previous song, so back button does cause a skips (by default)
- Skips expire with a rolling window after a configurable number of days
- Removed songs go to an archive playlist before they are removed permanently for a custom time
- Dry run to log and setup
- Handles rate limiting and missed polls gracefully

## Requirements

- Python 3.11 or newer
- spotipy (install with `pip install spotipy` in a virtual environment)
- A Spotify account and a free app from the [Spotify developer dashboard](https://developer.spotify.com/dashboard)


## Setup

1. In your Spotify app. Add `http://127.0.0.1:8888/callback` as a redirect URI.
2. Copy `config.example.toml` to `config.toml` and fill in your client ID, client secret, archive playlist ID and the playlists to prune.
3. Log in once on a machine with a browser: `python skip_pruner.py --auth`. This creates `.spotify_cache`.
4. Run it: `python skip_pruner.py`

Keep `dry_run = true` until the log shows what you expect, then set it to `false` and restart.

## Running on a server

Do step 3 on your own pc, then you can copy `skip_pruner.py`, `config.toml` and `.spotify_cache` to a server. Run it as a systemd service:

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

# Media Hub

A personal YouTube video and MP3 downloader rebuilt for Termux and the AYCF admin hub. Mobile-first, local-only Flask + Waitress interface on **127.0.0.1:8083**, powered by yt-dlp and FFmpeg.

## Install on your phone

After this rebuild is merged into `master`:

```bash
cd ~
git clone https://github.com/aliahmed7866/Django-youtube-video-and-mp3-downloader.git
cd Django-youtube-video-and-mp3-downloader
bash termux/install-service.sh
```

For an existing checkout, switch to `master`, pull with `git pull --ff-only`, then run the installer. The installer expects the existing AYCF admin hub at `~/aycf-trip-planner`. Override `AYCF_APP_DIR`, `AYCF_CONFIG_DIR`, or `AYCF_ADMIN_REGISTRY` for a custom installation.

Open **http://127.0.0.1:8083**, or refresh your admin hub on port 8079. Start, stop and restart work through its existing controls. No extra password is needed. The service only binds to loopback and checks browser mutation requests with CSRF tokens.

The installer preserves existing registry entries and backs up the registry before adding Media Hub. An older AYCF installer that overwrites `apps.json` can remove the card: rerun `python termux/register.py` to restore it. The companion AYCF registry update prevents this with the default port.

## Everyday use

Paste a YouTube video, Shorts or youtu.be link; choose MP4 (up to 360/480/720/1080p) or MP3 (128/192/320 kbps). Downloads queue immediately, then run one at a time. Video quality is a ceiling; a lower available MP4 format may be selected. MP3 bitrate does not improve the original source quality.

Progress covers individual media streams and can restart when audio begins. “Finishing your file” means FFmpeg is merging or converting. Save completed files through your browser into Android's Downloads. Retry failed/cancelled jobs, filter/search recent history, or remove files you no longer need. Playlists and live streams are excluded.

The initial view includes 200 jobs with active downloads always first; Show more history expands this up to 5,000 jobs. Counts cover all stored jobs; search and filters apply to the loaded history. Queued work is processed oldest first. At most 20 active/queued jobs are accepted. Each source stream is limited to 2 GB, and a download times out after two hours; the combined output may be larger. Downloads are refused if free space is below 256 MB. These are guardrails, not a storage quota. Failed partial files remain until their job is removed.

Pause queue stops upcoming downloads while allowing the current download to finish. The pause setting persists across service restarts. Resume queue restarts processing. Adding the same video, format and quality twice while it is active is rejected; a different quality is allowed. The browser remembers your last successfully selected format and quality when local storage is available. Titles appear as soon as the downloader retrieves them, and common failures include recovery advice instead of raw extractor output.

## Storage and service management

- Data and downloads: `~/.local/share/mediahub` (outside the checkout).
- Private configuration: `~/.config/mediahub/env`.
- Service log: `~/.local/state/mediahub/current`.
- Deployment result: `~/.local/state/mediahub/deploy-status.txt`.
- Deployment logs: `~/.local/state/mediahub/deploy/current`.
- Health: `http://127.0.0.1:8083/health` (database, worker and dependency availability).

```bash
sv status mediahub mediahub-deploy
sv restart mediahub
sv down mediahub
cat ~/.local/state/mediahub/deploy-status.txt
```

The `mediahub-deploy` service checks `master` every five minutes, fast-forwards clean checkouts, installs requirements, restarts the app and records success only after its health check. Dirty checkouts, other branches, and divergent history are left alone. Failed deployments are reported and retried; there is no automatic rollback. Stop the watcher with `sv down mediahub-deploy` if you want to keep the app stopped during updates. Manual update: `bash termux/update.sh`.

Edit `MEDIAHUB_PORT` in the private env file and rerun the installer to change ports. For first installation, `MEDIAHUB_PORT=8083 bash termux/install-service.sh` sets it. `MEDIAHUB_DATA_DIR` and `MEDIAHUB_ADMIN_URL` are also configurable there. Termux services resume when its service supervisor starts. Android reboot startup requires your existing Termux:Boot setup to start that supervisor; Android battery restrictions can still suspend Termux.

When restarted, queued jobs remain queued and interrupted jobs become failed with a retry action. Download subprocesses are terminated on normal service shutdown. The process lock prevents duplicate workers using the same data directory.

YouTube can change extraction behaviour or require account verification. Update the extractor when needed:

```bash
.venv/bin/python -m pip install --upgrade 'yt-dlp[default]'
sv restart mediahub
```

The installer includes Node.js and FFmpeg; yt-dlp's bundled EJS dependency handles current YouTube JavaScript extraction. See the [yt-dlp documentation](https://github.com/yt-dlp/yt-dlp) for upstream limitations. Download content you own or have permission to save.

## Development

Python 3.10+; FFmpeg and Node.js for real downloads.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q
.venv/bin/python run.py
```

Tests use simulated downloader subprocesses and temporary data; they do not download from YouTube. The previous Django/pytube/moviepy implementation is retained in Git history. Its committed database is no longer tracked or used; existing local files are not migrated into the new queue.

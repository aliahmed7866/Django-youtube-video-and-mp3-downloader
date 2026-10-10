# Media Hub

A personal YouTube, TikTok, Instagram and X/Twitter video and MP3 downloader rebuilt for Termux and the AYCF admin hub. Mobile-first, local-only Flask + Waitress interface on **127.0.0.1:8083**, powered by yt-dlp and FFmpeg.

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

Paste a YouTube video, Shorts, youtu.be, TikTok video or official TikTok short link; choose MP4 (up to 360/480/720/1080p) or MP3 (128/192/320 kbps). Downloads queue immediately, then run one at a time. Video quality is a ceiling; a lower available MP4 format may be selected. MP3 bitrate does not improve the original source quality.

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

## TikTok support

Individual public video URLs (`www.tiktok.com/@creator/video/…`) and official short links (`vm.tiktok.com`, `vt.tiktok.com`, `www.tiktok.com/t/…`) use the same queue, MP4 and MP3 options. Tracking parameters are discarded. Profiles, photo slideshows and live streams are excluded. For TikTok portrait video, the quality ceiling uses the shorter edge (for example, 720 × 1280 fits 720p). If TikTok offers no format within the limit, choose a higher quality.

Short links are resolved by yt-dlp in the background. Different short links pointing to the same video are not deduplicated before resolution. The extractor set is restricted to the supported individual-post extractors. Watermark-free output is not guaranteed. Availability depends on TikTok's regional, login and extraction restrictions; no account or cookie handling is added. See [yt-dlp's TikTok extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/tiktok.py). Live TikTok downloads need verification on the phone.

## Instagram, playback and phone sharing

Paste public Instagram Reel/video-post links (`instagram.com/reel/…`, `/reels/…`, `/p/…`, or legacy `/tv/…`). Links with a creator name before the post type are also accepted. Tracking parameters are removed and shortcode aliases normalize to one URL. Captions containing one supported link can be pasted directly. Multiple different links are rejected so one job always represents one requested video.

Instagram carousels save the first video entry only. Photo-only posts cannot produce a video. Profiles, Stories, live streams and Instagram `/share/…` short links are not supported in this release; use the original Reel/post URL. Instagram may require login, restrict anonymous access or rate-limit requests. No credentials or cookie import is included. MP4 and MP3 use the same queue and retries; quality for TikTok and Instagram is limited by the shorter edge for portrait clips.

Completed downloads now have **Play/Listen**, **Save file** and **Original** actions. Playback supports byte-range requests and runs in a dialog independently of queue polling. Source badges and a platform filter help browse the loaded history. Audio and video quality preferences are remembered separately.

### Share from Android through Termux

Run this once after updating:

```bash
cd "$HOME/Django-youtube-video-and-mp3-downloader"
.venv/bin/python termux/install-share.py
```

Choose **Share → Termux** in the source app. Media Hub opens with the link filled in; choose the format and tap Add download. The bridge reads the current port from `~/.config/mediahub/env`, including a custom port such as 8084. It never executes that configuration file as code. Both Termux and the Media Hub service need to be running.

The setup preserves an existing `~/bin/termux-url-opener` and explains how to use the fallback if that hook belongs to another app. It does not take over an existing share workflow. For custom configuration locations, export `MEDIAHUB_CONFIG_DIR` in your share-launch environment.

### Add Media Hub to the home screen

Use **Add to phone** when offered, or your browser's Install/Add to Home screen menu. The manifest includes a GET share target that only fills the form; no download is queued until you submit it. Direct appearance in Android's share sheet depends on the browser and installation mode, particularly for localhost apps. Use the Termux bridge when direct sharing is unavailable.

The service worker never caches session pages, APIs or media. It displays an offline explanation if the service is stopped, keeping the shared URL available for a retry. Local-only hosting still applies.

### Installation reliability

The installer now waits for runit's supervision FIFO before enabling either the app or updater, avoiding the initial `unable to open supervise/ok` race. It also checks that FFmpeg actually executes and reports `pkg upgrade` guidance for broken native libraries. The existing saved port and admin registration are preserved.

### Instagram video works differently from audio

Instagram may publish MP4 video and M4A audio as separate streams. Media Hub now merges these with FFmpeg and also accepts combined video/audio files. Known dimensions within the selected short-edge limit are preferred; if the platform supplies no dimensions, an MP4 stream can still be selected, but its resolution cannot be guaranteed in advance. If only a known higher-resolution format is available, choose a higher video quality rather than repeatedly retrying the same limit.

## X / Twitter

Paste a public video-post URL from `x.com` or `twitter.com`, including mobile links and shared captions. Tracking parameters are removed and both domains normalize to the same post URL for duplicate detection. A post downloads its first video; a link ending in `/video/2` (or another positive index) preserves that media selection.

MP4 and MP3 use the existing queue, playback, retries and phone sharing. X/Twitter has its own collection filter. MP4 quality uses the shorter-edge ceiling, supports split or combined streams, and accepts silent MP4 clips; silent clips cannot produce MP3 audio. Profiles, search, Spaces, photo links and `t.co` redirects are excluded; copy the original post link. Private or login-restricted posts may fail; no credentials or cookie import is added. Live X extraction still needs verification on the phone.

## Local video editor

Open **Media Hub → Video editor**, or visit `http://127.0.0.1:8083/editor`
(use your configured Media Hub port). It uses the same service and Admin Hub
controls as the downloader; no new service, account, API, or subscription is needed.

- Import videos from your device with the Android/browser file picker; add multiple
  files, reorder with the arrow buttons, or add the same imported clip twice.
- Trim with start/end times in seconds, change speed from **0.25× to 4×**, and set
  each clip's volume from mute to 200%. Audio speed changes preserve pitch.
- Choose landscape, portrait, or square output at 480p, 720p, or 1080p. Mixed sizes
  fit within the frame with padding, without cropping. Phone rotation metadata is
  handled by FFmpeg during export.
- Add local music, choose its volume, and mix it with the clips. Music loops to the
  end; mute each clip to use music alone.
- Preview individual clips or the sequence before export. This quick preview uses
  original browser-playable files, trim points and speed; final framing, music and
  volume boosts are visible/audible in the finished MP4 preview.
- Export to an MP4 with H.264 video and AAC stereo audio at 30 fps, then preview or
  download it. Export progress and cancellation are available on the page.

The editor copies imports into `MEDIAHUB_DATA_DIR/editor` (normally
`~/.local/share/mediahub/editor`). Your original files are never edited or deleted.
Uploads and processing stay on the same device when using the local Termux URL.
Imports and completed exports persist until you delete their copies on the page.
The timeline saves in this browser's local storage; clearing browser/site storage
removes that draft. Export recipes and job status are also stored on disk.

**Limits and practical notes:** up to 2 GB per imported file, 50 timeline clips,
two hours per output, and three active/queued exports. Only one export runs at a
time, with limited FFmpeg threads to reduce phone memory pressure. A conservative
free-space check reserves room for temporary segments and the final MP4. Imported
files, temporary upload buffering, and exports require additional device storage.
Start with 720p or 480p on your phone. Keep Termux running; Android can stop long
jobs if its battery restrictions apply. After a service restart, an interrupted
export is marked failed with instructions to export the saved timeline again;
queued exports remain queued. Temporary segments are cleaned after completion,
cancellation, or failure. A clip unsupported by your browser may still export if
FFmpeg can decode it. HDR-to-SDR tone mapping is not provided in this first version.

To install an update **after the editor PR is merged**:

```sh
cd ~/Django-youtube-video-and-mp3-downloader
bash termux/update.sh
```

Use your actual checkout directory if different. The updater fast-forwards `master`,
installs dependencies, refreshes the Admin Hub entry, and restarts the service. The
existing automatic deployment watcher can also apply merged updates. FFmpeg is
already included in the Termux installer (`pkg install ffmpeg` repairs a missing
installation). `/health` reports the downloader and editor worker status.

Editor tests include real FFmpeg imports and exports (trim, speed, mixed aspect
ratios, audio, silent clips and looping music), downloaded-file byte ranges,
cancellation of a running process, restart recovery, queue limits, bad media,
request limits, CSRF/Host checks, path isolation and storage checks. Run:

```sh
python -m pytest -q
```

Install FFmpeg/ffprobe first to run the media integration tests; CI installs them.
The filter pipeline follows the [FFmpeg filter documentation](https://ffmpeg.org/ffmpeg-filters.html)
for `setpts`, `atempo`, `apad`, scaling and audio mixing.

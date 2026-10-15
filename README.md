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

Open **Media Hub → Video editor** or `http://127.0.0.1:8083/editor` (use your
configured port). Local videos and music stay on the device running Termux. No
account, external API or paid service is used. Imported copies leave your originals
untouched. Reorder or reuse clips, trim start/end, set 0.25×–4× speed, adjust volume
or mute, layer music/voiceovers, preview the timeline, then export/download.

### Layered timeline

The video sequence is gapless: drag a video block to reorder it, or drag its left or
right handle to trim or restore footage within the original source. Following
videos and their linked audio move together. The linked audio lane shows the sound
belonging to each video; choose **Detach audio** to mute that video and place its
sound on an independent audio track for separate trims and movement.

Add music or voiceovers to **A1, A2 or A3**. Drag a block horizontally to set when it
starts, or vertically to change tracks. Audio on all three tracks mixes together,
including overlaps on the same track. Edge handles trim source audio; extending a
handle restores available source. To repeat a section, enable **Loop** in its
controls: source in/out chooses the repeating section, and the edge handles then
change the loop's occupied timeline length. Audio beyond the final video is omitted.
Moving/reordering video does not move detached audio or other independent audio.

Tap the ruler or move the Playhead slider to seek. Select a clip and use **Split at
playhead**, **Duplicate**, or exact source in/out, speed, volume and position fields.
Looped audio must have looping turned off before splitting. **Zoom**, **Fit**,
optional edge/playhead snapping and **Undo/Redo** help with precise edits. Arrow
keys on trim handles adjust 0.05 output seconds (Shift: 0.5); arrow keys on clip
bodies move audio or reorder video. Ctrl/Cmd+Z undoes timeline edits outside inputs.
Edits auto-save; undo history lasts until this page closes. Deleting an imported
copy also removes its clips and clears undo history. This is a single video
sequence with layered audio; visual overlays and compositing are not included.

### Reverse clips

Select a video or audio clip and enable **Reverse** in its controls. The selected
source in/out section plays backward; speed and volume still apply. A video's
linked audio reverses with it. Use **Detach audio** to control its direction
separately, or set the clip volume to zero for silent reverse footage. Independent
audio can also be reversed, including a trimmed section repeated with Loop.

Timeline edge handles and Split follow playback direction: trimming the left edge
of a reversed clip removes material from its source out point. Source in/out
fields always describe the original file. Reverse survives duplicates, undo/redo,
draft saves and export.

Reverse video is prepared in short lossless chunks on disk; decoded audio sample
frames are reversed in bounded disk reads. This avoids buffering an entire long
clip in RAM, but needs extra temporary storage and processing time. Lossless export
retains the selected decoded video frames in reverse order without changing their
pixels. Sparse variable-frame-rate footage retains its frames; preview timing is
approximate. Browser preview uses silent backward frame seeking. Reversed audio
is omitted from quick preview and is heard in the exported file. Exported Instagram
MP4s can be previewed with sound in the page; lossless masters need a compatible player.

### Quality choices

**Lossless master is the default.** It uses FFV1 video with uncompressed 64-bit
floating-point PCM audio in MKV. There is no resizing, chroma reduction, bit-depth
reduction or forced frame-rate conversion. All retained source frames are encoded;
speed changes modify their timestamps. Exact decoded pixel hashes are tested for
8-bit, 10-bit and 4:4:4 sources, including 60 fps footage and speed changes. Unchanged
PCM audio is compared sample-for-sample. Trimming removes content; speed, volume
and music edits intentionally alter the output. Lossless encoding does not mean
an edited file is byte-identical to its original compressed source.

To avoid silent conversions, a lossless timeline requires matching source dimensions,
orientation, pixel format, sample aspect ratio and color settings. Audio sources
(including music) need matching sample rates, channel counts and layouts. Export
incompatible clips separately, or explicitly choose the Instagram copy. Unsupported
native pixel formats fail instead of falling back to a lower-quality format. HDR
(including PQ, HLG and detected Dolby Vision/mastering metadata) is rejected in both
profiles until color-managed output is supported. Keep the original HDR footage.
Lossless MKV files need a compatible player, such as VLC; direct Instagram upload
and browser preview are not offered for this archival format.

**Instagram MP4 is a separate, explicitly lossy choice.** A confirmation checkbox is
required before the server will queue it. The canvas choices are Reels/Stories
1080×1920, Feed 1080×1350, square 1080×1080, or landscape 1920×1080. Sources fit
without cropping, using Lanczos scaling and padding. Output is H.264 High profile,
CRF 16, medium preset, with a 20 Mbps maximum rate / 40 Mbps buffer and AAC stereo
256 kbps / 48 kHz. It uses 30 fps, or 60 fps when source frame rates exceed 30 fps.
This preset prioritizes quality but is **not lossless**: resizing, conversion to
8-bit 4:2:0, frame-rate normalization and H.264/AAC encoding can change the result.
Upscaling cannot create source detail. Instagram can recompress uploaded videos;
no export preset can guarantee that Instagram preserves the original quality.

Each segment's audio remains uncompressed until the final export, including when
mixing music, avoiding a second AAC generation. Encoded video segments are joined
without another video encode. Keep a lossless master and make a separate Instagram
copy for sharing. The source-backed design references are the
[FFV1 specification](https://github.com/FFmpeg/FFV1),
[FFmpeg filter documentation](https://ffmpeg.org/ffmpeg-filters.html), and
[Meta's description of Instagram video encodes](https://engineering.fb.com/2022/11/04/video-engineering/instagram-video-processing-encoding-reduction/).

### Storage, preview and updates

Imports and completed exports live in `MEDIAHUB_DATA_DIR/editor` (normally
`~/.local/share/mediahub/editor`) until deleted through the page. The timeline saves
in this browser's local storage; clearing site storage removes that draft. Export
recipes and status persist on disk. Quick preview uses original browser-playable
clips, trim points, speed and the audio layers. Browser timing is approximate;
final framing, audio limiting and volume boosts are applied on export. Finished Instagram MP4s can be previewed in the page.

Limits: 2 GB per imported file, 50 video clips, 20 independent audio regions across
three audio tracks, two hours per output, three
active/queued exports, and one running export. Lossless masters can be very large:
a conservative disk-space check allows for native decoded video, audio,
intermediate segments and the final file. The worker also checks remaining free
space while processing. Source copies and temporary upload buffering need extra
storage. Keep Termux running and allow time for high-quality encoding on a phone.
Interrupted exports are marked failed after a restart; export the saved draft again.
Queued jobs from the older compressed-only editor are marked failed on upgrade so
that a quality profile must be selected explicitly. Existing completed MP4 downloads
remain available. Intermediate files are cleaned after completion/failure/cancellation.

After merging an editor update, run from your Media Hub checkout:

```sh
cd ~/Django-youtube-video-and-mp3-downloader
bash termux/update.sh
```

The existing deployment watcher can also apply merged updates. FFmpeg is installed
by the Termux setup (`pkg install ffmpeg` repairs a missing installation). `/health`
reports the downloader and editor worker status. Run `python -m pytest -q` with
FFmpeg/ffprobe installed for real media integration tests; CI installs them.
Run `node --test tests/timeline_model.test.cjs` for timeline geometry tests.

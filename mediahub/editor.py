"""Local, disk-backed video editing. One export at a time keeps phone RAM bounded."""
from contextlib import contextmanager
import json
import math
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid

from flask import Blueprint, abort, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import RequestEntityTooLarge


UPLOAD_LIMIT = 2 * 1024**3
RESERVE = 256 * 1024**2
ACTIVE = ('queued', 'rendering', 'cancelling')
REVERSE_CHUNK_BYTES = 32 * 1024**2

FORMATS = 'mov,matroska,webm,avi,mpegts,mpeg,flv,ogg,mp3,wav,aac,flac'
INPUT_OPTIONS = ['-protocol_whitelist', 'file,pipe', '-format_whitelist', FORMATS]


def number(value, low, high, label):
    if isinstance(value, bool):
        raise ValueError(f'Invalid {label}.')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'Invalid {label}.') from None
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f'{label} must be between {low:g} and {high:g}.')
    return result


def reverse_flag(clip):
    value = clip.get('reverse', False)
    if type(value) is not bool:
        raise ValueError('Reverse must be on or off.')
    return value


def probe(path, kind):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', *INPUT_OPTIONS,
                                 '-show_format', '-show_streams', '-of', 'json', str(path)],
                                capture_output=True, timeout=30, check=True)
        data = json.loads(result.stdout)
        videos = [s for s in data['streams'] if s['codec_type'] == 'video'
                  and not s.get('disposition', {}).get('attached_pic')]
        audios = [s for s in data['streams'] if s['codec_type'] == 'audio']
        stream = (videos if kind == 'video' else audios)[0]
        duration = number(stream.get('duration', data['format'].get('duration')), .05, 86400, 'Duration')
        video = videos[0] if videos else {}
        audio = audios[0] if audios else {}
        rotation = next((s.get('rotation', 0) for s in video.get('side_data_list', [])
                         if 'rotation' in s), 0)
        return dict(duration=duration, width=stream.get('width', 0), height=stream.get('height', 0),
                    video_index=videos[0]['index'] if videos else None,
                    audio_index=audios[0]['index'] if audios else None, has_audio=bool(audios),
                    pixel_format=video.get('pix_fmt'), rotation=rotation,
                    frame_rate=video.get('avg_frame_rate', '0/1'),
                    sar=video.get('sample_aspect_ratio', '1:1'),
                    color_range=video.get('color_range', 'unknown'),
                    color_space=video.get('color_space', 'unknown'),
                    color_transfer=video.get('color_transfer', 'unknown'),
                    color_primaries=video.get('color_primaries', 'unknown'),
                    sample_rate=int(audio.get('sample_rate', 48000)), channels=audio.get('channels', 2),
                    channel_layout=audio.get('channel_layout') or {1: 'mono', 2: 'stereo'}.get(audio.get('channels'), ''),
                    hdr=video.get('color_transfer') in ('smpte2084', 'arib-std-b67')
                    or any('DOVI' in s.get('side_data_type', '') or 'Mastering display' in s.get('side_data_type', '')
                           for s in video.get('side_data_list', [])))
    except FileNotFoundError:
        raise ValueError('Install FFmpeg in Termux: pkg install ffmpeg') from None
    except (subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
        raise ValueError(f'Cannot read this {kind}. Choose a playable local media file (MP4/MOV/WebM or MP3/WAV).') from None


def tempo(speed):
    filters = []
    while speed < .5:
        filters.append('atempo=0.5')
        speed /= .5
    while speed > 2:
        filters.append('atempo=2')
        speed /= 2
    return ','.join(filters + [f'atempo={speed:g}'])


def frame_rate(source):
    try:
        a, b = source['frame_rate'].split('/')
        rate = float(a) / float(b)
        return rate if math.isfinite(rate) and rate > 0 else 60
    except (ValueError, ZeroDivisionError, KeyError):
        return 60


def bytes_per_pixel(pixel_format):
    # Native sample planes; 10/12/16-bit samples occupy two bytes in decoded frames.
    for name, factor in [('yuv420p', 1.5), ('yuv422p', 2), ('yuv444p', 3), ('gbrp', 3)]:
        if pixel_format.startswith(name):
            return factor * (1 if pixel_format == name else 2)
    return 8  # Conservative fallback for alpha/RGB/other formats.


class Cancelled(Exception):
    pass


class Editor:
    def __init__(self, root):
        self.root = root / 'editor'
        self.root.mkdir(exist_ok=True, mode=0o700)
        self.assets = self.root / 'assets'
        self.exports = self.root / 'exports'
        self.assets.mkdir(exist_ok=True)
        self.exports.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.upload_lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name='video-editor', daemon=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, metadata TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS exports (
                id TEXT PRIMARY KEY, recipe TEXT NOT NULL, status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', created REAL NOT NULL)''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / 'editor.sqlite3', timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def asset(self, ident):
        if not isinstance(ident, str):
            raise ValueError('Choose an imported file.')
        with self.connect() as db:
            row = db.execute('SELECT metadata FROM assets WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('An imported file is missing. Add it again.')
        return json.loads(row[0])

    def asset_path(self, ident):
        # IDs originate here, never filenames or filesystem paths supplied by the client.
        if not isinstance(ident, str) or len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
            raise ValueError('Invalid file ID.')
        return self.assets / ident

    def job(self, ident):
        with self.connect() as db:
            row = db.execute('SELECT * FROM exports WHERE id=?', (ident,)).fetchone()
        return dict(row) if row else None

    def update(self, ident, **fields):
        assert fields.keys() <= {'status', 'progress', 'error'}
        with self.connect() as db:
            db.execute('UPDATE exports SET ' + ','.join(k + '=?' for k in fields) + ' WHERE id=?',
                       (*fields.values(), ident))

    def validate(self, body):
        if not isinstance(body, dict) or not isinstance(body.get('clips'), list) or not 1 <= len(body['clips']) <= 50:
            raise ValueError('Add between 1 and 50 clips.')
        profile = body.get('profile', 'lossless')
        if profile not in ('lossless', 'instagram'):
            raise ValueError('Choose Lossless master or Instagram MP4.')
        if profile == 'instagram' and body.get('allow_lossy') is not True:
            raise ValueError('Instagram MP4 uses lossy compression. Confirm that choice, or export a lossless master.')
        shape = body.get('shape', 'portrait')
        if shape not in ('landscape', 'portrait', 'square', 'feed'):
            raise ValueError('Choose a valid frame shape.')
        clips = []
        sources = []
        for clip in body['clips']:
            if not isinstance(clip, dict):
                raise ValueError('Invalid clip.')
            asset = self.asset(clip.get('asset_id'))
            if asset['kind'] != 'video':
                raise ValueError('Timeline clips must be videos.')
            # Re-probe also upgrades assets imported by older editor versions.
            sources.append(probe(self.asset_path(asset['id']), 'video'))
            start = number(clip.get('start', 0), 0, asset['duration'], 'Start')
            end = number(clip.get('end', asset['duration']), 0, asset['duration'], 'End')
            if end - start < .05 - 1e-9:
                raise ValueError('Each clip needs at least 0.05 seconds; end must be after start.')
            speed = number(clip.get('speed', 1), .25, 4, 'Speed')
            volume = number(clip.get('volume', 1), 0, 2, 'Volume')
            clips.append(dict(asset_id=asset['id'], start=start, end=end, speed=speed, volume=volume, reverse=reverse_flag(clip)))
        duration = sum((c['end'] - c['start']) / c['speed'] for c in clips)
        if duration > 7200:
            raise ValueError('Keep an export under two hours. Split longer projects into separate exports.')
        audio_clips = self.validate_audio(body, duration)
        if any(s['hdr'] for s in sources):
            raise ValueError('HDR footage needs color-managed handling. This editor currently accepts SDR footage only; it will not silently flatten HDR colors.')
        native = sources[0]
        if not native['pixel_format']:
            raise ValueError('Cannot determine the source pixel format safely. Choose another video.')
        audio_sources = [s for s in sources if s['has_audio']]
        for region in audio_clips:
            audio_sources.append(probe(self.asset_path(region['asset_id']), 'audio'))
        audio = audio_sources[0] if audio_sources else dict(sample_rate=48000, channels=2, channel_layout='stereo')
        if profile == 'lossless':
            keys = ('width', 'height', 'rotation', 'pixel_format', 'sar', 'color_range',
                    'color_space', 'color_transfer', 'color_primaries')
            if any(any(s[k] != native[k] for k in keys) for s in sources):
                raise ValueError('Lossless master needs clips with matching dimensions, orientation, pixel format and color settings. Export them separately or explicitly choose Instagram MP4 to resize/convert.')
            if native['rotation'] not in (0, 90, -90, 180, -180, 270, -270):
                raise ValueError('This rotation would resample pixels. Use footage with a right-angle rotation for a lossless master.')
            if any((s['sample_rate'], s['channels'], s['channel_layout']) !=
                   (audio['sample_rate'], audio['channels'], audio['channel_layout']) for s in audio_sources):
                raise ValueError('Lossless master needs matching audio sample rates and channel layouts; export separately or choose Instagram MP4.')
        return dict(clips=clips, profile=profile, resolution='1080', shape=shape, duration=duration,
                    audio_clips=audio_clips, music_id=None,
                    native=native, audio=audio, fps=60 if any(frame_rate(s) > 30.1 for s in sources) else 30,
                    music_volume=number(body.get('music_volume', .3), 0, 2, 'Music volume'))

    def validate_audio(self, body, duration):
        raw = body.get('audio_clips')
        if raw is None:
            # Preserve old saved jobs and API clients using one looping music bed.
            music_id = body.get('music_id')
            raw = [] if not music_id else [dict(asset_id=music_id, loop=True, length=duration,
                                               volume=body.get('music_volume', .3))]
        if not isinstance(raw, list) or len(raw) > 20:
            raise ValueError('Use at most 20 independent audio clips.')
        regions = []
        for clip in raw:
            if not isinstance(clip, dict):
                raise ValueError('Invalid audio clip.')
            asset = self.asset(clip.get('asset_id'))
            source = probe(self.asset_path(asset['id']), 'audio')
            start = number(clip.get('start', 0), 0, source['duration'], 'Audio start')
            end = number(clip.get('end', source['duration']), 0, source['duration'], 'Audio end')
            if end - start < .05 - 1e-9:
                raise ValueError('Each audio clip needs at least 0.05 seconds.')
            speed = number(clip.get('speed', 1), .25, 4, 'Audio speed')
            at = number(clip.get('at', 0), 0, 7200, 'Timeline position')
            track = clip.get('track', 1)
            if type(track) is not int or track not in (1, 2, 3):
                raise ValueError('Choose audio track 1, 2 or 3.')
            loop = clip.get('loop', False)
            if type(loop) is not bool:
                raise ValueError('Choose whether to loop the audio.')
            natural = (end - start) / speed
            length = number(clip.get('length', natural) if loop else natural, .01, 7200, 'Audio length')
            if at + length > 7200:
                raise ValueError('Keep audio within the two-hour timeline limit.')
            regions.append(dict(asset_id=asset['id'], start=start, end=end, speed=speed, at=at,
                                volume=number(clip.get('volume', 1), 0, 2, 'Audio volume'),
                                track=track, loop=loop, length=length, reverse=reverse_flag(clip)))
        return regions

    def enqueue(self, body):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            raise ValueError('Install FFmpeg in Termux: pkg install ffmpeg')
        with self.lock:
            recipe = self.validate(body)
            # Lossless output can approach raw size. Include segments and final file.
            native = recipe['native']
            if recipe['profile'] == 'lossless':
                pixels = native['width'] * native['height']
                raw_bytes = 0
                for clip in recipe['clips']:
                    source = probe(self.asset_path(clip['asset_id']), 'video')
                    raw_bytes += pixels * bytes_per_pixel(source['pixel_format']) * frame_rate(source) * (clip['end'] - clip['start'])
                needed = 2 * raw_bytes + recipe['duration'] * recipe['audio']['sample_rate'] * recipe['audio']['channels'] * 16
            else:
                needed = recipe['duration'] * 8_000_000
            audio_seconds = sum(min((c['end'] - c['start']) / c['speed'], c['length'], recipe['duration'] - c['at'])
                                for c in recipe['audio_clips'] if c['at'] < recipe['duration'] and c['volume'] > 0)
            rate, count = (recipe['audio']['sample_rate'], recipe['audio']['channels']) if recipe['profile'] == 'lossless' else (48000, 2)
            needed += audio_seconds * rate * count * 8
            for clip in recipe['clips']:
                if clip['reverse']:
                    source = probe(self.asset_path(clip['asset_id']), 'video')
                    seconds = clip['end'] - clip['start']
                    needed += seconds * source['width'] * source['height'] * bytes_per_pixel(source['pixel_format']) * frame_rate(source)
                    if clip['volume'] and source['has_audio']:
                        needed += seconds * source['sample_rate'] * source['channels'] * 16
            for region in recipe['audio_clips']:
                if region['reverse'] and region['volume'] and region['at'] < recipe['duration']:
                    source = probe(self.asset_path(region['asset_id']), 'audio')
                    needed += (region['end'] - region['start']) * source['sample_rate'] * source['channels'] * 16
            free = shutil.disk_usage(self.root).free
            if free < RESERVE + needed:
                raise ValueError(f'This export reserves about {(RESERVE + needed) / 1024**3:.1f} GB including temporary files; '
                                 f'{free / 1024**3:.1f} GB is free. Free storage or shorten the timeline.')
            ident = uuid.uuid4().hex
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT count(*) FROM exports WHERE status IN ('queued','rendering','cancelling')").fetchone()[0] >= 3:
                    raise ValueError('Three exports are already waiting. Let one finish first.')
                db.execute('INSERT INTO exports (id,recipe,status,created) VALUES (?,?,?,?)',
                           (ident, json.dumps(recipe), 'queued', time.time()))
        return self.job(ident)

    def start(self):
        with self.connect() as db:
            for row in db.execute("SELECT id,recipe FROM exports WHERE status='queued'").fetchall():
                if 'profile' not in json.loads(row['recipe']):
                    db.execute("UPDATE exports SET status='failed', error='Quality settings changed. Select an export profile and export your saved timeline again.' WHERE id=?", (row['id'],))
            db.execute("UPDATE exports SET status='failed', error='Export interrupted by a service restart. Export your saved timeline again.' WHERE status='rendering'")
            db.execute("UPDATE exports SET status='cancelled' WHERE status='cancelling'")
            settled = db.execute("SELECT id FROM exports WHERE status IN ('failed','cancelled')").fetchall()
        for row in settled:
            shutil.rmtree(self.exports / row[0], ignore_errors=True)
        for path in self.assets.glob('*.part'):
            path.unlink(missing_ok=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            with self.lock, self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT * FROM exports WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if row:
                    db.execute("UPDATE exports SET status='rendering' WHERE id=?", (row['id'],))
            if row:
                self.render(dict(row))
            else:
                self.stop.wait(.5)

    def check(self, ident):
        if self.stop.is_set():
            raise RuntimeError('Export interrupted. Start Media Hub and export the saved timeline again.')
        if self.job(ident)['status'] == 'cancelling':
            raise Cancelled()
        if shutil.disk_usage(self.root).free < RESERVE:
            raise RuntimeError('Storage is almost full. Free some space and export again.')

    def execute(self, ident, args, directory, duration, offset, weight):
        self.check(ident)
        progress = directory / 'progress.txt'
        progress.write_text('')
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
               '-filter_threads', '1', '-filter_complex_threads', '1', '-progress', str(progress)] + args
        with (directory / 'ffmpeg.log').open('wb') as log:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
            try:
                while proc.poll() is None:
                    self.check(ident)
                    with progress.open('rb') as f:
                        f.seek(max(0, progress.stat().st_size - 4096))
                        lines = f.read().decode('utf-8', 'replace').splitlines()
                    times = [line.split('=', 1)[1] for line in lines if line.startswith('out_time_us=')]
                    if times:
                        try:
                            fraction = min(1, max(0, float(times[-1]) / 1_000_000 / duration))
                            self.update(ident, progress=min(99, offset + weight * fraction))
                        except ValueError:
                            pass
                    self.stop.wait(.25)
                if proc.returncode:
                    raise RuntimeError('FFmpeg could not export this media with the selected quality. Details are in the export log.')
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()

    def reverse_audio(self, ident, asset, start, end, directory, prefix, offset, weight=0):
        """Decode once, reverse whole sample frames on disk with bounded Python memory."""
        source = probe(self.asset_path(asset['id']), 'audio')
        raw, target = directory / f'{prefix}-forward.pcm', directory / f'{prefix}-reverse.pcm'
        args = ['-ss', str(start), *INPUT_OPTIONS, '-i', str(self.asset_path(asset['id'])),
                '-map', f"0:{asset['audio_index']}", '-vn', '-af',
                f'aresample=osf=dbl,atrim=duration={end - start},asetpts=PTS-STARTPTS,apad,atrim=duration={end - start}',
                '-t', str(end - start), '-c:a', 'pcm_f64le', '-ar', str(source['sample_rate']),
                '-ac', str(source['channels']), '-f', 'f64le', str(raw)]
        self.execute(ident, args, directory, end - start, offset, weight / 2)
        frame_bytes = source['channels'] * 8
        with raw.open('rb') as src, target.open('wb') as dst:
            remaining = total = raw.stat().st_size
            if remaining % frame_bytes:
                raise RuntimeError('Could not reverse complete audio sample frames.')
            block = max(1, 1024**2 // frame_bytes) * frame_bytes
            while remaining:
                self.check(ident)
                size = min(block, remaining); remaining -= size
                src.seek(remaining); data = src.read(size)
                if len(data) != size:
                    raise RuntimeError('Audio data changed during reversal.')
                dst.write(b''.join(data[i:i + frame_bytes] for i in range(size - frame_bytes, -1, -frame_bytes)))
                self.update(ident, progress=min(99, offset + weight / 2 + weight / 2 * (1 - remaining / total)))
        raw.unlink()
        return ['-f', 'f64le', '-ar', str(source['sample_rate']), '-ac', str(source['channels']), '-i', str(target)]

    def reverse_video(self, ident, asset, start, end, directory, prefix, offset, weight=0):
        """Reverse short lossless chunks and read them in reverse source order."""
        source = probe(self.asset_path(asset['id']), 'video')
        rate = frame_rate(source)
        frame_bytes = source['width'] * source['height'] * bytes_per_pixel(source['pixel_format'])
        # Target 32 MiB of frame planes, plus decoder/filter overhead. At least one frame.
        chunk = max(1 / rate, min(2, REVERSE_CHUNK_BYTES / max(1, frame_bytes * rate)))
        count = max(1, math.ceil((end - start) / chunk))
        entries, pending = [], 0
        for index in range(count):
            left = start + (end - start) * (count - index - 1) / count
            right = start + (end - start) * (count - index) / count
            name = f'{prefix}-reverse-{index}.mkv'
            args = ['-copyts', '-start_at_zero', '-threads', '2', '-ss', str(left), *INPUT_OPTIONS, '-i', str(self.asset_path(asset['id'])),
                    '-map', f"0:{asset['video_index']}", '-an', '-vf',
                    f'trim=start={left}:end={right},reverse,setpts=PTS-STARTPTS',
                    '-c:v', 'ffv1', '-level', '3', '-slicecrc', '1', '-threads', '2',
                    '-pix_fmt', '+' + source['pixel_format'], '-fps_mode', 'passthrough',
                    '-enc_time_base', '1:1000000', '-map_metadata', '-1', str(directory / name)]
            self.execute(ident, args, directory, right - left, offset + weight * index / count, weight / count)
            self.update(ident, progress=min(99, offset + weight * (index + 1) / count))
            # Sparse variable-frame-rate footage can have intervals containing no frames.
            frames = [line.split('=', 1)[1] for line in (directory / 'progress.txt').read_text().splitlines()
                      if line.startswith('frame=')]
            pending += right - left
            if frames and int(frames[-1]) == 0:
                (directory / name).unlink()
                continue
            entries.append([name, pending]); pending = 0
        if not entries:
            raise ValueError('The selected reverse section contains no video frames. Extend the trim.')
        entries[-1][1] += pending
        listing = directory / f'{prefix}-reverse.txt'
        listing.write_text(''.join(f"file '{name}'\nduration {duration:.9f}\n" for name, duration in entries))
        return ['-f', 'concat', '-safe', '1', '-protocol_whitelist', 'file,pipe', '-i', str(listing)]

    def render(self, job):
        ident = job['id']
        directory = self.exports / ident
        status, error = 'failed', ''
        try:
            directory.mkdir(exist_ok=True)
            recipe = json.loads(job['recipe'])
            lossless = recipe['profile'] == 'lossless'
            extension = 'mkv' if lossless else 'mp4'
            width, height = {'landscape': (1920, 1080), 'portrait': (1080, 1920),
                             'square': (1080, 1080), 'feed': (1080, 1350)}[recipe['shape']]
            sample_rate = recipe['audio']['sample_rate'] if lossless else 48000
            channels = recipe['audio']['channels'] if lossless else 2
            layout = recipe['audio']['channel_layout'] or f'{channels}c'
            if not lossless:
                layout = 'stereo'
            elapsed = 0
            for i, clip in enumerate(recipe['clips']):
                asset = self.asset(clip['asset_id'])
                duration = (clip['end'] - clip['start']) / clip['speed']
                offset = 90 * elapsed / recipe['duration']
                weight = 90 * duration / recipe['duration']
                video_label, audio_label = f"0:{asset['video_index']}", f"0:{asset['audio_index']}"
                if clip.get('reverse', False):
                    args = self.reverse_video(ident, asset, clip['start'], clip['end'], directory, f'v{i}', offset, weight * .4)
                    video_label = '0:0'
                    if asset['has_audio'] and clip['volume']:
                        args += self.reverse_audio(ident, asset, clip['start'], clip['end'], directory, f'v{i}', offset + weight * .4, weight * .1)
                        audio_label = '1:0'
                else:
                    args = ['-threads', '2', '-ss', str(clip['start']), *INPUT_OPTIONS,
                            '-i', str(self.asset_path(asset['id']))]
                vfilter = (f"[{video_label}]trim=duration={clip['end'] - clip['start']},"
                           f"setpts=(PTS-STARTPTS)/{clip['speed']}")
                if not lossless:
                    vfilter += (f',scale=iw*sar:ih,setsar=1,scale={width}:{height}:flags=lanczos:'
                                f'force_original_aspect_ratio=decrease:force_divisible_by=2,'
                                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={recipe['fps']},"
                                f'format=yuv420p,tpad=stop_mode=clone:stop_duration={duration},trim=duration={duration}')
                vfilter += '[v]'
                if asset['has_audio'] and clip['volume']:
                    effects = []
                    if clip['speed'] != 1:
                        effects.append(tempo(clip['speed']))
                    if clip['volume'] != 1:
                        effects.append(f"volume={clip['volume']}:precision=double")
                    if not lossless:
                        effects.append('aresample=48000')
                    effect = ','.join(effects)
                    afilter = (f"[{audio_label}]aresample=osf=dbl,atrim=duration={clip['end'] - clip['start']},"
                               'asetpts=PTS-STARTPTS,' + (effect + ',' if effect else '') +
                               f'apad,atrim=duration={duration}[a]')
                else:
                    afilter = f'anullsrc=r={sample_rate}:cl={layout},aresample=osf=dbl,atrim=duration={duration}[a]'
                args += ['-filter_complex', vfilter + ';' + afilter, '-map', '[v]', '-map', '[a]',
                         '-t', str(duration), '-threads', '2']
                if lossless:
                    # Enforce the native pixel format: fail instead of silently reducing bit depth/chroma.
                    args += ['-c:v', 'ffv1', '-level', '3', '-slicecrc', '1',
                             '-pix_fmt', '+' + recipe['native']['pixel_format'], '-fps_mode', 'passthrough',
                             '-enc_time_base', '1:1000000']
                    for key, option in [('color_range', '-color_range'), ('color_space', '-colorspace'),
                                        ('color_transfer', '-color_trc'), ('color_primaries', '-color_primaries')]:
                        if recipe['native'][key] != 'unknown':
                            args += [option, recipe['native'][key]]
                else:
                    args += ['-c:v', 'libx264', '-preset', 'medium', '-crf', '16',
                             '-profile:v', 'high', '-maxrate', '20M', '-bufsize', '40M']
                # Intermediate audio is uncompressed, so music never causes a second AAC generation.
                args += ['-c:a', 'pcm_f64le', '-ar', str(sample_rate), '-ac', str(channels),
                         '-map_metadata', '-1', str(directory / f'{i}.mkv')]
                self.execute(ident, args, directory, duration, offset + weight * (.5 if clip.get('reverse') else 0),
                             weight * (.5 if clip.get('reverse') else 1))
                for path in directory.glob(f'v{i}-*'):
                    path.unlink()
                elapsed += duration
            # Only internally generated basenames enter the concat list.
            (directory / 'clips.txt').write_text(''.join(
                f"file '{i}.mkv'\nduration {(c['end'] - c['start']) / c['speed']:.9f}\n"
                for i, c in enumerate(recipe['clips'])))
            args = ['-f', 'concat', '-safe', '1', '-protocol_whitelist', 'file,pipe', '-i', str(directory / 'clips.txt')]
            regions = recipe.get('audio_clips')
            if regions is None:
                regions = self.validate_audio(recipe, recipe['duration'])
            audible = [c for c in regions if c['at'] < recipe['duration'] and c['volume'] > 0]
            filters, labels = [], ['[0:a]']
            for index, region in enumerate(audible, 1):
                audio_asset = self.asset(region['asset_id'])
                length = min(region['length'], recipe['duration'] - region['at'])
                natural = min((region['end'] - region['start']) / region['speed'], length)
                # Trim first, then loop only that selected section, never the whole source.
                audio_file = directory / f'audio-{index}.wav'
                effects = []
                if region['speed'] != 1:
                    effects.append(tempo(region['speed']))
                if region['volume'] != 1:
                    effects.append(f"volume={region['volume']}:precision=double")
                effect = ','.join(effects)
                if region.get('reverse', False):
                    audio_args = self.reverse_audio(ident, audio_asset, region['start'], region['end'], directory,
                                                    f'a{index}', 90 + 4 * (index - 1) / len(audible), 2 / len(audible))
                    audio_label = '0:0'
                else:
                    audio_args = ['-ss', str(region['start']), *INPUT_OPTIONS, '-i', str(self.asset_path(audio_asset['id']))]
                    audio_label = f"0:{audio_asset['audio_index']}"
                audio_args += ['-map', audio_label, '-vn', '-af',
                              f"aresample=osf=dbl,atrim=duration={region['end'] - region['start']},asetpts=PTS-STARTPTS," +
                              (effect + ',' if effect else '') +
                              f'apad,atrim=duration={natural}', '-t', str(natural), '-c:a', 'pcm_f64le',
                              '-ar', str(sample_rate), '-ac', str(channels), '-rf64', 'auto', str(audio_file)]
                self.execute(ident, audio_args, directory, natural,
                             90 + (4 * (index - 1) + (2 if region.get('reverse') else 0)) / len(audible),
                             (2 if region.get('reverse') else 4) / len(audible))
                for path in directory.glob(f'a{index}-*'):
                    path.unlink()
                if region['loop']:
                    args += ['-stream_loop', '-1']
                args += [*INPUT_OPTIONS, '-i', str(audio_file)]
                filters.append(f'[{index}:a]atrim=duration={length},asetpts=PTS-STARTPTS[r{index}]')
                if region['at']:
                    # Generate leading silence as a stream, avoiding a huge delay buffer on phones.
                    filters += [f"anullsrc=r={sample_rate}:cl={layout},atrim=duration={region['at']}[s{index}]",
                                f'[s{index}][r{index}]concat=n=2:v=0:a=1[t{index}]']
                    labels.append(f'[t{index}]')
                else:
                    labels.append(f'[r{index}]')
            if audible:
                filters.append(''.join(labels) + f'amix=inputs={len(labels)}:duration=first:normalize=0,'
                               'alimiter=limit=0.95:level=0:latency=1[a]')
                args += ['-filter_complex', ';'.join(filters), '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy']
            else:
                args += ['-map', '0:v:0', '-map', '0:a:0', '-c:v', 'copy']
            args += ['-c:a', 'pcm_f64le'] if lossless else ['-c:a', 'aac', '-b:a', '256k']
            args += ['-ar', str(sample_rate), '-ac', str(channels), '-t', str(recipe['duration']), '-map_metadata', '-1']
            if not lossless:
                args += ['-movflags', '+faststart']
            args += [str(directory / f'result.{extension}')]
            self.execute(ident, args, directory, recipe['duration'], 94 if audible else 90, 6 if audible else 10)
            self.check(ident)
            status = 'complete'
        except Cancelled:
            status = 'cancelled'
        except Exception as exc:
            error = str(exc)[:500]
        finally:
            # Publish a settled status only after cleanup, so deletion cannot race it.
            with self.lock:
                if self.job(ident)['status'] == 'cancelling':
                    status = 'cancelled'
                if directory.exists():
                    for path in directory.iterdir():
                        if path.name != 'ffmpeg.log' and not (status == 'complete' and path.name == f'result.{extension}'):
                            path.unlink(missing_ok=True)
                self.update(ident, status=status, error=error,
                            progress=100 if status == 'complete' else self.job(ident)['progress'])


def install_editor(app, root):
    editor = Editor(root)
    app.extensions['editor'] = editor
    bp = Blueprint('editor', __name__)

    @bp.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @bp.errorhandler(RequestEntityTooLarge)
    def too_large(exc):
        return jsonify(error='Timeline request is too large.' if request.endpoint == 'editor.export'
                       else 'Each imported file must be 2 GB or smaller.'), 413

    @bp.get('/editor')
    def page():
        session.setdefault('csrf', secrets.token_hex(24))
        return render_template('editor.html', csrf=session['csrf'])

    @bp.get('/api/editor')
    def state():
        with editor.connect() as db:
            assets = [json.loads(r[0]) for r in db.execute('SELECT metadata FROM assets ORDER BY rowid')]
            jobs = [dict(r) for r in db.execute('SELECT * FROM exports ORDER BY created DESC')]
        for job in jobs:
            recipe = json.loads(job.pop('recipe'))
            job['profile'] = recipe.get('profile', 'legacy')
            job['extension'] = 'mkv' if job['profile'] == 'lossless' else 'mp4'
        return jsonify(assets=assets, jobs=jobs, free_bytes=shutil.disk_usage(root).free,
                       ready=bool(shutil.which('ffmpeg') and shutil.which('ffprobe')), max_upload=UPLOAD_LIMIT)

    @bp.post('/api/editor/assets')
    def upload():
        kind = request.args.get('kind', 'video')
        if kind not in ('audio', 'video'):
            raise ValueError('Choose video or audio.')
        name = request.args.get('name', 'Imported clip')[:180]
        request.max_content_length = UPLOAD_LIMIT
        if not editor.upload_lock.acquire(blocking=False):
            return jsonify(error='Another file is importing. Wait for it to finish.'), 409
        ident = uuid.uuid4().hex
        part = editor.assets / (ident + '.part')
        target = editor.asset_path(ident)
        try:
            size = 0
            with part.open('xb') as f:
                while chunk := request.stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > UPLOAD_LIMIT:
                        raise RequestEntityTooLarge()
                    if shutil.disk_usage(root).free < len(chunk) + RESERVE:
                        raise ValueError('Not enough free storage to import this file.')
                    f.write(chunk)
            metadata = dict(id=ident, name=name, kind=kind, size=size, **probe(part, kind))
            with editor.lock, editor.connect() as db:
                part.replace(target)
                db.execute('INSERT INTO assets VALUES (?,?)', (ident, json.dumps(metadata)))
            return jsonify(metadata), 201
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            part.unlink(missing_ok=True)
            editor.upload_lock.release()

    @bp.get('/api/editor/assets/<ident>/file')
    def asset_file(ident):
        asset = editor.asset(ident)
        path = editor.asset_path(ident)
        if not path.is_file():
            abort(404)
        return send_file(path, download_name=asset['name'], conditional=True)

    @bp.delete('/api/editor/assets/<ident>')
    def delete_asset(ident):
        with editor.lock, editor.connect() as db:
            editor.asset(ident)
            rows = db.execute("SELECT recipe FROM exports WHERE status IN ('queued','rendering','cancelling')")
            for row in rows:
                recipe = json.loads(row[0])
                if recipe.get('music_id') == ident or any(c['asset_id'] == ident for c in recipe['clips'] + recipe.get('audio_clips', [])):
                    return jsonify(error='This file is used by an active export. Wait or cancel it first.'), 409
            editor.asset_path(ident).unlink(missing_ok=True)
            db.execute('DELETE FROM assets WHERE id=?', (ident,))
        return '', 204

    @bp.post('/api/editor/exports')
    def export():
        request.max_content_length = 64 * 1024
        return jsonify(editor.enqueue(request.get_json(silent=True))), 202

    @bp.post('/api/editor/exports/<ident>/cancel')
    def stop_export(ident):
        with editor.lock, editor.connect() as db:
            if not editor.job(ident):
                abort(404)
            db.execute("UPDATE exports SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancelling' END WHERE id=? AND status IN ('queued','rendering')", (ident,))
        return jsonify(editor.job(ident))

    @bp.get('/api/editor/exports/<ident>/file')
    def result(ident):
        job = editor.job(ident)
        if not job or job['status'] != 'complete':
            abort(404)
        lossless = json.loads(job['recipe']).get('profile') == 'lossless'
        extension = 'mkv' if lossless else 'mp4'
        return send_file(editor.exports / ident / f'result.{extension}',
                         mimetype='video/x-matroska' if lossless else 'video/mp4',
                         as_attachment=request.args.get('play') != '1',
                         download_name=f'Media-Hub-{ident[:8]}.{extension}', conditional=True)

    @bp.delete('/api/editor/exports/<ident>')
    def delete_export(ident):
        with editor.lock, editor.connect() as db:
            job = editor.job(ident)
            if not job:
                abort(404)
            if job['status'] in ACTIVE:
                return jsonify(error='Wait for the export to finish cancelling first.'), 409
            shutil.rmtree(editor.exports / ident, ignore_errors=True)
            db.execute('DELETE FROM exports WHERE id=?', (ident,))
        return '', 204

    app.register_blueprint(bp)

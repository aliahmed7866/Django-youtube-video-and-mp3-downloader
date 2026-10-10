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
        return dict(duration=duration, width=stream.get('width', 0), height=stream.get('height', 0),
                    video_index=videos[0]['index'] if videos else None,
                    audio_index=audios[0]['index'] if audios else None, has_audio=bool(audios))
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
        resolution, shape = body.get('resolution', '720'), body.get('shape', 'landscape')
        if resolution not in ('480', '720', '1080') or shape not in ('landscape', 'portrait', 'square'):
            raise ValueError('Choose a valid export size and shape.')
        clips = []
        for clip in body['clips']:
            if not isinstance(clip, dict):
                raise ValueError('Invalid clip.')
            asset = self.asset(clip.get('asset_id'))
            if asset['kind'] != 'video':
                raise ValueError('Timeline clips must be videos.')
            start = number(clip.get('start', 0), 0, asset['duration'], 'Start')
            end = number(clip.get('end', asset['duration']), 0, asset['duration'], 'End')
            if end - start < .05:
                raise ValueError('Each clip needs at least 0.05 seconds; end must be after start.')
            speed = number(clip.get('speed', 1), .25, 4, 'Speed')
            volume = number(clip.get('volume', 1), 0, 2, 'Volume')
            clips.append(dict(asset_id=asset['id'], start=start, end=end, speed=speed, volume=volume))
        duration = sum((c['end'] - c['start']) / c['speed'] for c in clips)
        if duration > 7200:
            raise ValueError('Keep an export under two hours. Split longer projects into separate exports.')
        music_id = body.get('music_id') or None
        if music_id and self.asset(music_id)['kind'] != 'audio':
            raise ValueError('Choose an audio file for background music.')
        return dict(clips=clips, resolution=resolution, shape=shape, duration=duration, music_id=music_id,
                    music_volume=number(body.get('music_volume', .3), 0, 2, 'Music volume'))

    def enqueue(self, body):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            raise ValueError('Install FFmpeg in Termux: pkg install ffmpeg')
        with self.lock:
            recipe = self.validate(body)
            # Intermediate segments plus final MP4 need room beyond imported originals.
            if shutil.disk_usage(self.root).free < RESERVE + recipe['duration'] * 2_000_000:
                raise ValueError('Not enough free space for this export. Remove old exports or shorten the timeline.')
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
                    raise RuntimeError('FFmpeg could not process this media. Try another clip or 480p. Details are in the export log.')
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()

    def render(self, job):
        ident = job['id']
        directory = self.exports / ident
        status, error = 'failed', ''
        try:
            directory.mkdir(exist_ok=True)
            recipe = json.loads(job['recipe'])
            side = int(recipe['resolution'])
            width, height = {'landscape': (side * 16 // 9 // 2 * 2, side),
                             'portrait': (side, side * 16 // 9 // 2 * 2), 'square': (side, side)}[recipe['shape']]
            elapsed = 0
            for i, clip in enumerate(recipe['clips']):
                asset = self.asset(clip['asset_id'])
                duration = (clip['end'] - clip['start']) / clip['speed']
                args = ['-threads', '2', '-ss', str(clip['start']), *INPUT_OPTIONS,
                        '-i', str(self.asset_path(asset['id']))]
                vfilter = (f"[0:{asset['video_index']}]trim=duration={clip['end'] - clip['start']},"
                           f"setpts=(PTS-STARTPTS)/{clip['speed']},"
                           f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,'
                           f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,'
                           f'format=yuv420p,tpad=stop_mode=clone:stop_duration={duration},trim=duration={duration}[v]')
                if asset['has_audio'] and clip['volume']:
                    afilter = (f"[0:{asset['audio_index']}]atrim=duration={clip['end'] - clip['start']},"
                               f"asetpts=PTS-STARTPTS,{tempo(clip['speed'])},volume={clip['volume']},"
                               f'aresample=48000,apad,atrim=duration={duration}[a]')
                else:
                    afilter = f'anullsrc=r=48000:cl=stereo,atrim=duration={duration}[a]'
                args += ['-filter_complex', vfilter + ';' + afilter, '-map', '[v]', '-map', '[a]',
                         '-t', str(duration), '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast',
                         '-crf', '23', '-c:a', 'aac', '-b:a', '128k', '-ar', '48000', '-ac', '2',
                         '-video_track_timescale', '90000', '-map_metadata', '-1', str(directory / f'{i}.mp4')]
                self.execute(ident, args, directory, duration, 90 * elapsed / recipe['duration'],
                             90 * duration / recipe['duration'])
                elapsed += duration
            # Only internally generated basenames enter the concat list.
            (directory / 'clips.txt').write_text(''.join(
                f"file '{i}.mp4'\nduration {(c['end'] - c['start']) / c['speed']:.9f}\n"
                for i, c in enumerate(recipe['clips'])))
            args = ['-f', 'concat', '-safe', '1', '-protocol_whitelist', 'file,pipe', '-i', str(directory / 'clips.txt')]
            if recipe['music_id']:
                music = self.asset(recipe['music_id'])
                args += ['-stream_loop', '-1', *INPUT_OPTIONS, '-i', str(self.asset_path(music['id'])),
                         '-filter_complex', f"[1:{music['audio_index']}]volume={recipe['music_volume']}[music];"
                         '[0:a][music]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:latency=1[a]',
                         '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k']
            else:
                args += ['-map', '0:v:0', '-map', '0:a:0', '-c', 'copy']
            args += ['-t', str(recipe['duration']), '-map_metadata', '-1', '-movflags', '+faststart',
                     str(directory / 'result.mp4')]
            self.execute(ident, args, directory, recipe['duration'], 90, 10)
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
                        if path.name != 'ffmpeg.log' and not (status == 'complete' and path.name == 'result.mp4'):
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
        return jsonify(error='Each imported file must be 2 GB or smaller.'), 413

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
            job.pop('recipe')
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
                if recipe['music_id'] == ident or any(c['asset_id'] == ident for c in recipe['clips']):
                    return jsonify(error='This file is used by an active export. Wait or cancel it first.'), 409
            editor.asset_path(ident).unlink(missing_ok=True)
            db.execute('DELETE FROM assets WHERE id=?', (ident,))
        return '', 204

    @bp.post('/api/editor/exports')
    def export():
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
        return send_file(editor.exports / ident / 'result.mp4', mimetype='video/mp4',
                         as_attachment=request.args.get('play') != '1',
                         download_name=f'Media-Hub-{ident[:8]}.mp4', conditional=True)

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

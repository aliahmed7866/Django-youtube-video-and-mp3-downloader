import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit


def command(job, directory):
    args = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist',
            '--use-extractors', 'Youtube,TikTok,vm[.]tiktok,Instagram',
            '--playlist-items', '1', '--no-live-from-start', '--match-filter', '!is_live', '--js-runtimes', 'node',
            '--socket-timeout', '20', '--retries', '3', '--fragment-retries', '3',
            '--max-filesize', '2G', '--newline', '--no-colors', '--progress',
            '--progress-template', 'download:PROGRESS:%(progress._percent_str)s',
            '--print', 'before_dl:TITLE:%(title)j',
            '--print', 'after_move:RESULT:%()j', '--no-simulate',
            '-o', str(directory / '%(title).150B [%(id)s].%(ext)s')]
    if job['kind'] == 'audio':
        args += ['-f', 'bestaudio/best', '-x', '--audio-format', 'mp3', '--audio-quality', job['quality']+'K']
    else:
        height = job['quality']
        host = urlsplit(job['url']).hostname or ''
        if host in ('tiktok.com', 'instagram.com') or host.endswith(('.tiktok.com', '.instagram.com')):
            # Instagram may expose separate DASH video/audio or omit dimensions.
            # Prefer known dimensions within the short-edge cap, then unknown dimensions.
            selectors = []
            for dimension_filter in (f'[width<={height}]', f'[height<={height}]', '[width=?0][height=?0]'):
                video = f'bv{dimension_filter}[ext=mp4]'
                combined = f'b{dimension_filter}[ext=mp4]'
                selectors.extend((f'{video}+ba[ext=m4a]', combined))
            formats = '/'.join(selectors)
        else:
            formats = f'bv*[height<={height}][ext=mp4]+ba[ext=m4a]/b[height<={height}][ext=mp4]'
        args += ['-f', formats, '--merge-output-format', 'mp4']
    return args + ['--', job['url']]


def friendly_error(message):
    lower = message.lower()
    if 'no video' in lower or 'no video formats' in lower:
        return 'This post has no downloadable video. Try a Reel or video post rather than a photo.'
    if 'empty media response' in lower or 'login required' in lower or 'rate-limit' in lower:
        return 'The platform is limiting access or requires login. Try a public video later; private-account downloads are not supported.'
    if 'sign in' in lower or 'not a bot' in lower or 'private video' in lower:
        return 'This platform requires account verification or this video is private. Try a publicly available video.'
    if 'requested format' in lower and ('not available' in lower or 'unavailable' in lower):
        return 'No MP4 video matched this quality. Try a higher video quality (for example 1080p). Audio may still be available.'
    if 'not available' in lower or 'unavailable' in lower or 'removed' in lower:
        return 'This video or format is unavailable. Try another video or a lower quality.'
    if 'timed out' in lower or 'connection' in lower or 'certificate' in lower:
        return 'Could not connect to the video platform. Check your connection and retry. If it continues, update yt-dlp.'
    if 'no space' in lower:
        return 'Your device has run out of space. Remove saved downloads or free storage, then retry.'
    if 'error:' in lower:
        return 'The platform could not complete this download. Retry or update yt-dlp; technical details are in the download log.'
    return message[-700:]


class Worker:
    def __init__(self, store):
        self.store = store
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name='downloads')

    def start(self):
        self.store.recover()
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            job = self.store.claim()
            if not job:
                self.stop.wait(1)
                continue
            try:
                self.download(job)
            except Exception as exc:
                if self.store.get(job['id'])['status'] in ('cancelled', 'cancelling'):
                    self.store.update(job['id'], status='cancelled')
                else:
                    self.store.update(job['id'], status='failed', error=friendly_error(str(exc)))

    def download(self, job):
        if not shutil.which('ffmpeg'):
            raise RuntimeError('FFmpeg is missing. Run pkg install ffmpeg in Termux.')
        directory = self.store.root / 'downloads' / job['id']
        directory.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(directory).free < 256 * 1024**2:
            raise RuntimeError('Less than 256 MB free. Free storage before retrying.')
        logfile = directory / 'download.log'
        result = None
        tail = ''
        started = time.monotonic()
        with logfile.open('w') as output, logfile.open() as reader:
            process = subprocess.Popen(command(job, directory), stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while True:
                    for line in reader:
                        tail = (tail + line)[-1500:]
                        if line.startswith('PROGRESS:'):
                            try:
                                progress = min(99, max(0, float(line.split(':', 1)[1].strip().strip('%'))))
                                self.store.update(job['id'], progress=progress)
                            except ValueError:
                                pass
                        elif line.startswith('TITLE:'):
                            self.store.update(job['id'], title=str(json.loads(line[6:])))
                        elif line.startswith('RESULT:'):
                            result = json.loads(line[7:])
                        elif '[ExtractAudio]' in line or '[Merger]' in line:
                            if self.store.get(job['id'])['status'] not in ('cancelled', 'cancelling'):
                                self.store.update(job['id'], status='processing')
                    cancelled = self.store.get(job['id'])['status'] in ('cancelled', 'cancelling')
                    if cancelled or self.stop.is_set() or time.monotonic() - started > 7200:
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGTERM)
                            try:
                                process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                os.killpg(process.pid, signal.SIGKILL)
                        if cancelled:
                            process.wait()
                            self.store.update(job['id'], status='cancelled')
                            return
                        raise RuntimeError('Download interrupted or exceeded two hours. Retry when ready.')
                    if process.poll() is not None:
                        # Read any buffered output written just before the process exited.
                        remaining = reader.read()
                        tail = (tail + remaining)[-1500:]
                        for line in remaining.splitlines():
                            if line.startswith('RESULT:'):
                                result = json.loads(line[7:])
                        break
                    self.stop.wait(.4)
                if process.returncode != 0 or not result:
                    raise RuntimeError(tail[-700:] or 'Download did not produce a file. Try again or update yt-dlp.')
                filename = result.get('filepath')
                if not filename:
                    raise RuntimeError('Downloader returned no output path.')
                path = (directory / filename).resolve()
                if not path.is_relative_to(directory.resolve()) or not path.is_file():
                    raise RuntimeError('Output file is missing or outside its download folder.')
                # Cancellation may arrive between the last poll and process completion.
                with self.store.connect() as db:
                    db.execute("UPDATE jobs SET status='complete',progress=100,title=?,filename=? WHERE id=? AND status NOT IN ('cancelled','cancelling')",
                               (result.get('title', path.stem), str(path.relative_to(self.store.root.resolve())), job['id']))
                if self.store.get(job['id'])['status'] == 'cancelling':
                    self.store.update(job['id'], status='cancelled')
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()

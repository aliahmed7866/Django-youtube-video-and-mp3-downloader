import hmac
import os
from pathlib import Path
import re
import secrets
import shutil
from urllib.parse import parse_qs, urlsplit

from flask import Flask, abort, jsonify, render_template, request, send_file, session
from .store import Store


def youtube_url(raw):
    if not isinstance(raw, str) or len(raw) > 2048:
        raise ValueError('Paste a valid YouTube video link.')
    parsed = urlsplit(raw.strip())
    if parsed.scheme not in ('https', 'http') or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        raise ValueError('Use a YouTube video link.')
    host = (parsed.hostname or '').lower()
    if host == 'youtu.be':
        ident = parsed.path.strip('/')
    elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
        if parsed.path == '/watch':
            ident = parse_qs(parsed.query).get('v', [''])[0]
        elif parsed.path.startswith(('/shorts/', '/live/', '/embed/')):
            ident = parsed.path.split('/')[2]
        else:
            ident = ''
    else:
        ident = ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', ident):
        raise ValueError('Paste a YouTube video, Shorts, or youtu.be link; playlists are not supported.')
    return 'https://www.youtube.com/watch?v=' + ident


def create_app(data_dir=None):
    app = Flask(__name__)
    root = Path(data_dir or os.environ.get('MEDIAHUB_DATA_DIR', '~/.local/share/mediahub')).expanduser().resolve()
    store = Store(root)
    secret = root / 'secret'
    try:
        with secret.open('x') as f:
            os.chmod(secret, 0o600)
            f.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    app.secret_key = secret.read_text()
    app.config.update(MAX_CONTENT_LENGTH=8192, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_NAME='mediahub_session')
    app.extensions['store'] = store

    @app.before_request
    def protect():
        if request.host.split(':')[0] not in ('127.0.0.1', 'localhost'):
            abort(403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            token = request.headers.get('X-CSRF-Token', '')
            if not token or not hmac.compare_digest(token.encode(), session.get('csrf', '').encode()):
                abort(403)

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'"
        return response

    @app.get('/')
    def index():
        session.setdefault('csrf', secrets.token_hex(24))
        return render_template('index.html', csrf=session['csrf'], admin_url=os.environ.get('MEDIAHUB_ADMIN_URL', 'http://127.0.0.1:8079'))

    @app.get('/health')
    def health():
        with store.connect() as db:
            db.execute('SELECT 1')
        worker = app.extensions.get('worker')
        healthy = bool(worker and worker.thread.is_alive())
        return jsonify(ok=healthy, service='mediahub', database='ready', worker='running' if healthy else 'stopped', ffmpeg=bool(shutil.which('ffmpeg')), node=bool(shutil.which('node'))), 200 if healthy else 503

    @app.get('/api/jobs')
    def jobs():
        return jsonify(jobs=store.list(), free_bytes=shutil.disk_usage(root).free)

    @app.post('/api/jobs')
    def add():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error='Expected a JSON object.'), 400
        try:
            url = youtube_url(body.get('url'))
            kind = body.get('kind', 'video')
            quality = str(body.get('quality', '720'))
            choices = {'video': {'360', '480', '720', '1080'}, 'audio': {'128', '192', '320'}}
            if kind not in choices or quality not in choices[kind]:
                raise ValueError('Choose a valid format and quality.')
            return jsonify(store.add(url, kind, quality)), 201
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.post('/api/jobs/<ident>/cancel')
    def cancel(ident):
        if not store.get(ident):
            abort(404)
        with store.connect() as db:
            db.execute("UPDATE jobs SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancelling' END WHERE id=? AND status IN ('queued','downloading','processing')", (ident,))
        return jsonify(store.get(ident))

    @app.post('/api/jobs/<ident>/retry')
    def retry(ident):
        job = store.get(ident)
        if not job:
            abort(404)
        if job['status'] not in ('failed', 'cancelled'):
            return jsonify(error='Only failed or cancelled downloads can be retried.'), 409
        try:
            return jsonify(store.add(job['url'], job['kind'], job['quality'])), 201
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.get('/api/jobs/<ident>/file')
    def file(ident):
        job = store.get(ident)
        if not job or job['status'] != 'complete' or not job['filename']:
            abort(404)
        path = (root / job['filename']).resolve()
        if not path.is_relative_to(root / 'downloads' / ident) or not path.is_file():
            abort(404)
        return send_file(path, as_attachment=True, download_name=path.name, conditional=True)

    @app.delete('/api/jobs/<ident>')
    def delete(ident):
        job = store.get(ident)
        if not job:
            abort(404)
        # Cancelled work may still be shutting down; delete only settled jobs.
        if job['status'] not in ('complete', 'failed', 'cancelled'):
            return jsonify(error='Wait for the download to finish cancelling before removing it.'), 409
        directory = root / 'downloads' / ident
        if directory.exists():
            shutil.rmtree(directory)
        with store.connect() as db:
            db.execute('DELETE FROM jobs WHERE id=?', (ident,))
        return '', 204

    return app

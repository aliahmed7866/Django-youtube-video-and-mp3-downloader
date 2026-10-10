"""Exercise the real local import/edit/export path, including FFmpeg output."""
import json
import shutil
import struct
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from mediahub.app import create_app
from mediahub.editor import Editor


@pytest.fixture(scope='module')
def media(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Real video tests require FFmpeg and ffprobe')
    root = tmp_path_factory.mktemp('media')
    common = ['ffmpeg', '-v', 'error', '-y', '-filter_threads', '1']
    subprocess.run(common + ['-f', 'lavfi', '-i', 'color=c=red:s=320x180:r=30:d=2',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                            '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                            str(root / 'red.mp4')], check=True)
    subprocess.run(common + ['-f', 'lavfi', '-i', 'color=c=blue:s=180x320:r=24:d=2',
                            '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(root / 'blue.mp4')], check=True)
    subprocess.run(common + ['-f', 'lavfi', '-i', 'sine=frequency=220:duration=0.4', str(root / 'music.wav')], check=True)
    return root


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path)
    app.testing = True
    client = app.test_client()
    assert client.get('/editor').status_code == 200
    with client.session_transaction() as session:
        headers = {'X-CSRF-Token': session['csrf']}
    return app.extensions['editor'], client, headers


def upload(setup, path, kind='video'):
    _, client, headers = setup
    response = client.post('/api/editor/assets', query_string={'name': path.name, 'kind': kind},
                           data=path.read_bytes(), content_type='application/octet-stream', headers=headers)
    assert response.status_code == 201, response.json
    return response.json


def wait_job(editor, ident):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        job = editor.job(ident)
        if job['status'] not in ('queued', 'rendering', 'cancelling'):
            return job
        time.sleep(.05)
    pytest.fail('Export did not finish')


@pytest.mark.parametrize('music,shape,fast,slow,dimensions,duration', [
    (False, 'portrait', 2, .5, (480, 852), 2.5),
    (True, 'landscape', 4, .25, (852, 480), 4.25),
    (True, 'square', 1, 1, (480, 480), 2),
])
def test_real_export_trim_speed_silence_music_and_download(setup, media, music, shape, fast, slow, dimensions, duration):
    editor, client, headers = setup
    red = upload(setup, media / 'red.mp4')
    blue = upload(setup, media / 'blue.mp4')
    body = {'clips': [dict(asset_id=red['id'], start=.5, end=1.5, speed=fast, volume=.5),
                      dict(asset_id=blue['id'], start=.25, end=1.25, speed=slow, volume=0)],
            'resolution': '480', 'shape': shape}
    if music:
        body['music_id'] = upload(setup, media / 'music.wav', 'audio')['id']
    response = client.post('/api/editor/exports', json=body, headers=headers)
    assert response.status_code == 202, response.json
    ident = response.json['id']
    assert client.delete(f"/api/editor/assets/{red['id']}", headers=headers).status_code == 409
    assert client.get(f'/api/editor/exports/{ident}/file').status_code == 404
    editor.start()
    try:
        job = wait_job(editor, ident)
        assert job['status'] == 'complete', (job, (editor.exports / ident / 'ffmpeg.log').read_text())
    finally:
        editor.stop.set(); editor.thread.join(5)
    path = editor.exports / ident / 'result.mp4'
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))
    video, audio = info['streams']
    assert (video['width'], video['height']) == dimensions
    assert video['codec_name'] == 'h264'
    assert audio['codec_name'] == 'aac' and audio['channels'] == 2
    assert float(info['format']['duration']) == pytest.approx(duration, abs=.15)
    # Real decoded pixels verify sequence order and mixed-aspect fitting.
    for when, channel in [(.2, 0), (1.5, 2)]:
        pixel = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(when), '-i', str(path),
                                         '-vf', 'crop=2:2:iw/2:ih/2', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', '-'])
        assert pixel[channel] > 200
    # The silent blue clip must stay silent unless looping background music was added.
    pcm = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', '1.5', '-i', str(path),
                                   '-t', '0.5', '-vn', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'])
    samples = struct.unpack('<' + 'h' * (len(pcm) // 2), pcm)
    rms = (sum(s * s for s in samples) / len(samples)) ** .5
    assert rms > 100 if music else rms < 10
    response = client.get(f'/api/editor/exports/{ident}/file')
    assert response.status_code == 200 and 'attachment' in response.headers['Content-Disposition']
    assert client.get(f'/api/editor/exports/{ident}/file?play=1', headers={'Range': 'bytes=0-99'}).status_code == 206
    assert not list(path.parent.glob('[0-9]*.mp4'))
    assert client.delete(f'/api/editor/exports/{ident}', headers=headers).status_code == 204
    assert not path.exists()
    assert client.delete(f"/api/editor/assets/{red['id']}", headers=headers).status_code == 204
    assert (media / 'red.mp4').exists()


def test_import_validation_limits_and_security(setup, media, monkeypatch):
    editor, client, headers = setup
    assert client.post('/api/editor/assets', data=b'x').status_code == 403
    assert client.get('/editor', headers={'Host': 'evil.example'}).status_code == 403
    assert client.post('/api/editor/assets', data=b'not video', headers=headers).status_code == 400
    playlist = b'#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1:8079/private\n'
    assert client.post('/api/editor/assets', data=playlist, headers=headers).status_code == 400
    assert not list(editor.assets.iterdir())
    # Import exceeds the existing small JSON limit without changing that limit elsewhere.
    asset = upload(setup, media / 'red.mp4')
    assert asset['size'] > 8192
    assert client.post('/api/jobs', data=b'x' * 9000, content_type='application/json', headers=headers).status_code == 413
    assert client.get('/api/editor/assets/not-a-real-file/file').status_code == 400
    monkeypatch.setattr('mediahub.editor.UPLOAD_LIMIT', 10)
    assert client.post('/api/editor/assets', data=b'x' * 11, headers=headers).status_code == 413
    assert not list(editor.assets.glob('*.part'))


@pytest.mark.parametrize('change', [{'start': -1}, {'end': 10}, {'start': 1, 'end': .5},
                                    {'speed': 0}, {'speed': float('nan')}, {'volume': float('inf')},
                                    {'asset_id': '../../secret'}, {'end': None}])
def test_bad_edits_rejected(setup, media, change):
    _, client, headers = setup
    asset = upload(setup, media / 'red.mp4')
    clip = dict(asset_id=asset['id'], start=0, end=1, speed=1, volume=1)
    clip.update(change)
    assert client.post('/api/editor/exports', json={'clips': [clip]}, headers=headers).status_code == 400


def test_queue_cancel_restart_and_disk_reserve(setup, media, monkeypatch):
    editor, client, headers = setup
    asset = upload(setup, media / 'red.mp4')
    body = {'clips': [dict(asset_id=asset['id'], start=0, end=1)]}
    jobs = [editor.enqueue(body) for _ in range(3)]
    assert client.post('/api/editor/exports', json=body, headers=headers).status_code == 400
    ident = jobs[0]['id']
    assert client.post(f'/api/editor/exports/{ident}/cancel', headers=headers).json['status'] == 'cancelled'
    editor.update(jobs[1]['id'], status='rendering')
    editor.update(jobs[2]['id'], status='cancelling')
    restarted = Editor(editor.root.parent)
    restarted.start()
    try:
        assert restarted.job(jobs[1]['id'])['status'] == 'failed'
        assert restarted.job(jobs[2]['id'])['status'] == 'cancelled'
    finally:
        restarted.stop.set(); restarted.thread.join(5)
    assert restarted.asset(asset['id'])['name'] == 'red.mp4'
    monkeypatch.setattr('mediahub.editor.shutil.disk_usage', lambda _: SimpleNamespace(free=0))
    assert client.post('/api/editor/exports', json=body, headers=headers).status_code == 400


def test_running_cancel_stops_ffmpeg(setup, media, monkeypatch):
    editor, client, headers = setup
    asset = upload(setup, media / 'red.mp4')
    job = editor.enqueue({'clips': [dict(asset_id=asset['id'], start=0, end=2)]})
    ident = job['id']
    editor.update(ident, status='rendering')
    started = threading.Event()
    processes = []
    original = subprocess.Popen
    def slow_process(args, **kwargs):
        # A real FFmpeg process deliberately runs in real time to exercise cancellation.
        proc = original(['ffmpeg', '-v', 'error', '-re', '-f', 'lavfi', '-i', 'color=d=60', '-f', 'null', '-'], **kwargs)
        processes.append(proc); started.set(); return proc
    monkeypatch.setattr('mediahub.editor.subprocess.Popen', slow_process)
    thread = threading.Thread(target=editor.render, args=(job,))
    thread.start()
    assert started.wait(5)
    assert client.delete(f'/api/editor/exports/{ident}', headers=headers).status_code == 409
    client.post(f'/api/editor/exports/{ident}/cancel', headers=headers)
    thread.join(8)
    assert not thread.is_alive()
    assert processes[0].poll() is not None
    assert editor.job(ident)['status'] == 'cancelled'
    assert not (editor.exports / ident / 'result.mp4').exists()

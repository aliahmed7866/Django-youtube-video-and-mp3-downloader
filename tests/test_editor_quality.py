"""Compare decoded samples, rather than trusting a 'lossless' encoder label."""
import json
import subprocess

import pytest

from test_editor import setup, media, upload, wait_job


def frame_hashes(path):
    output = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0',
                                      '-fps_mode', 'passthrough', '-f', 'framemd5', '-'], text=True)
    return [line.rsplit(',', 1)[1].strip() for line in output.splitlines() if not line.startswith('#')]


def audio_samples(path):
    return subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0',
                                    '-c:a', 'pcm_f64le', '-f', 'f64le', '-'])


@pytest.mark.parametrize('pixel_format,speed', [('yuv420p', 1), ('yuv420p10le', .5), ('yuv444p', 4)])
def test_native_lossless_frames_and_audio(setup, tmp_path, pixel_format, speed):
    editor, client, headers = setup
    source = tmp_path / 'source.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-filter_threads', '1',
                    '-f', 'lavfi', '-i', 'testsrc2=s=192x108:r=60:d=1',
                    '-f', 'lavfi', '-i', 'sine=frequency=330:sample_rate=44100:duration=1',
                    '-c:v', 'ffv1', '-pix_fmt', pixel_format, '-c:a', 'pcm_s16le', str(source)], check=True)
    asset = upload(setup, source)
    response = client.post('/api/editor/exports', headers=headers, json={
        'clips': [dict(asset_id=asset['id'], start=0, end=1, speed=speed)]})
    assert response.status_code == 202, response.json
    ident = response.json['id']
    assert json.loads(response.json['recipe'])['profile'] == 'lossless'
    editor.start()
    try:
        job = wait_job(editor, ident)
        assert job['status'] == 'complete', (job, (editor.exports / ident / 'ffmpeg.log').read_text())
    finally:
        editor.stop.set(); editor.thread.join(5)
    target = editor.exports / ident / 'result.mkv'
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(target)]))
    v, a = info['streams']
    assert v['codec_name'] == 'ffv1' and v['pix_fmt'] == pixel_format
    assert (v['width'], v['height']) == (192, 108)
    assert a['codec_name'] == 'pcm_f64le' and a['sample_rate'] == '44100' and a['channels'] == 1
    original = frame_hashes(source)
    assert len(original) == 60
    assert frame_hashes(target) == original  # Includes every source frame, even after a speed change.
    if speed == 1:
        assert audio_samples(target) == audio_samples(source)
    result = client.get(f'/api/editor/exports/{ident}/file')
    assert result.status_code == 200 and result.mimetype == 'video/x-matroska'
    assert result.headers['Content-Disposition'].endswith('.mkv')
    state = client.get('/api/editor').json
    assert state['jobs'][0]['extension'] == 'mkv'


def test_explicit_lossy_consent_and_mismatched_sources(setup, media):
    _, client, headers = setup
    red = upload(setup, media / 'red.mp4')
    blue = upload(setup, media / 'blue.mp4')
    body = {'clips': [{'asset_id': red['id']}, {'asset_id': blue['id']}]}
    response = client.post('/api/editor/exports', json=body, headers=headers)
    assert response.status_code == 400 and 'matching dimensions' in response.json['error']
    body['profile'] = 'instagram'
    response = client.post('/api/editor/exports', json=body, headers=headers)
    assert response.status_code == 400 and 'lossy compression' in response.json['error']
    body['allow_lossy'] = True
    response = client.post('/api/editor/exports', json=body, headers=headers)
    assert response.status_code == 202
    assert json.loads(response.json['recipe'])['resolution'] == '1080'


def test_old_completed_download_and_old_queued_export(setup, media):
    editor, client, headers = setup
    asset = upload(setup, media / 'red.mp4')
    job = editor.enqueue({'clips': [{'asset_id': asset['id']}]})
    recipe = json.loads(job['recipe']); recipe.pop('profile')
    with editor.connect() as db:
        db.execute('UPDATE exports SET recipe=? WHERE id=?', (json.dumps(recipe), job['id']))
    editor.start()
    try:
        assert editor.job(job['id'])['status'] == 'failed'
    finally:
        editor.stop.set(); editor.thread.join(5)
    directory = editor.exports / job['id']; directory.mkdir()
    (directory / 'result.mp4').write_bytes(b'previous MP4')
    editor.update(job['id'], status='complete')
    assert client.get(f"/api/editor/exports/{job['id']}/file").data == b'previous MP4'


def test_lossless_concat_music_and_silent_clip(setup, media, tmp_path):
    editor, client, headers = setup
    red = upload(setup, media / 'red.mp4')
    silent = tmp_path / 'silent.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(media / 'red.mp4'), '-map', '0:v:0', '-c', 'copy', str(silent)], check=True)
    silent_asset = upload(setup, silent)
    music = upload(setup, media / 'music.wav', 'audio')
    job = editor.enqueue({'clips': [dict(asset_id=red['id'], start=0, end=.5),
                                    dict(asset_id=silent_asset['id'], start=.5, end=1)],
                          'music_id': music['id']})
    editor.start()
    try:
        completed = wait_job(editor, job['id'])
        assert completed['status'] == 'complete', (completed, (editor.exports / job['id'] / 'ffmpeg.log').read_text())
    finally:
        editor.stop.set(); editor.thread.join(5)
    target = editor.exports / job['id'] / 'result.mkv'
    assert frame_hashes(target) == frame_hashes(media / 'red.mp4')[:30]
    assert len(audio_samples(target)) > 44100 * 8 * .9


def test_hdr_rejected_and_high_frame_rate_instagram_plan(setup, media, tmp_path):
    editor, client, headers = setup
    hdr = tmp_path / 'hdr.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(media / 'red.mp4'), '-c', 'copy',
                    '-bsf:v', 'h264_metadata=transfer_characteristics=16', str(hdr)], check=True)
    asset = upload(setup, hdr)
    for profile in ('lossless', 'instagram'):
        response = client.post('/api/editor/exports', headers=headers, json={
            'profile': profile, 'allow_lossy': True, 'clips': [{'asset_id': asset['id']}]})
        assert response.status_code == 400 and 'HDR' in response.json['error']
    fast = tmp_path / 'fast.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-filter_threads', '1', '-i', str(media / 'red.mp4'),
                    '-r', '60', '-c:v', 'libx264', '-threads', '1', '-c:a', 'copy', str(fast)], check=True)
    asset = upload(setup, fast)
    recipe = editor.validate({'profile': 'instagram', 'allow_lossy': True, 'clips': [{'asset_id': asset['id']}]})
    assert recipe['fps'] == 60

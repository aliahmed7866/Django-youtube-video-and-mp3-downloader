"""Verify layered audio in actual exports, including sample timing and trimmed loops."""
import array
import json
import math
import subprocess

import pytest

from test_editor import setup, media, upload, wait_job
from test_editor_quality import frame_hashes


def render(editor, body):
    job = editor.enqueue(body)
    editor.render(job)
    done = editor.job(job['id'])
    directory = editor.exports / job['id']
    assert done['status'] == 'complete', (done, (directory / 'ffmpeg.log').read_text())
    assert not list(directory.glob('audio-*.wav'))
    return directory / 'result.mkv'


def samples(path):
    data = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0',
                                    '-c:a', 'pcm_f64le', '-f', 'f64le', '-'])
    result = array.array('d'); result.frombytes(data)
    return result


def rms(data, start, end):
    segment = data[round(start * 44100):round(end * 44100)]
    return math.sqrt(sum(x * x for x in segment) / len(segment))


def test_trim_move_and_overlap_audio_preserves_video(setup, media):
    editor, client, headers = setup
    video = upload(setup, media / 'red.mp4')
    audio = upload(setup, media / 'music.wav', 'audio')
    body = {'clips': [dict(asset_id=video['id'], end=2, volume=0)], 'audio_clips': [
        dict(asset_id=audio['id'], start=.1, end=.3, at=.5, volume=.4, track=1),
        dict(asset_id=audio['id'], start=.1, end=.3, at=.6, volume=.4, track=2),
        dict(asset_id=audio['id'], start=.1, end=.3, at=3, volume=1, track=3)]}
    job = editor.enqueue(body)
    assert client.delete(f"/api/editor/assets/{audio['id']}", headers=headers).status_code == 409
    editor.render(job)
    assert editor.job(job['id'])['status'] == 'complete'
    path = editor.exports / job['id'] / 'result.mkv'
    data = samples(path)
    assert rms(data, .1, .4) < 1e-8
    assert rms(data, .52, .58) > .03
    # Same phase, two simultaneous regions: sum doubles, with no automatic gain normalization.
    assert rms(data, .62, .68) == pytest.approx(2 * rms(data, .52, .58), rel=.05)
    assert rms(data, .85, 1.8) < 1e-8
    assert frame_hashes(path) == frame_hashes(media / 'red.mp4')


def test_loop_repeats_trimmed_section_only_and_stops_at_region_end(setup, media, tmp_path):
    editor, _, _ = setup
    video = upload(setup, media / 'red.mp4')
    source = tmp_path / 'two-tones.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=f=440:r=44100:d=0.2',
                    '-f', 'lavfi', '-i', 'sine=f=880:r=44100:d=0.2',
                    '-filter_complex', '[0:a][1:a]concat=n=2:v=0:a=1[a]', '-map', '[a]', str(source)], check=True)
    audio = upload(setup, source, 'audio')
    path = render(editor, {'clips': [dict(asset_id=video['id'], volume=0)], 'audio_clips': [
        dict(asset_id=audio['id'], start=.2, end=.4, at=.25, loop=True, length=1.2, volume=1)]})
    data = samples(path)
    for start in (.3, .55, .8, 1.05, 1.3):
        segment = data[round(start * 44100):round((start + .1) * 44100)]
        crossings = sum(a <= 0 < b for a, b in zip(segment, segment[1:]))
        assert crossings == pytest.approx(88, abs=2)
    assert rms(data, 0, .2) < 1e-8 and rms(data, 1.5, 1.9) < 1e-8


def test_detached_video_audio_speed_and_trim(setup, media):
    editor, _, _ = setup
    video = upload(setup, media / 'red.mp4')
    path = render(editor, {'clips': [dict(asset_id=video['id'], volume=0)], 'audio_clips': [
        dict(asset_id=video['id'], start=.4, end=1.4, speed=2, at=.75, volume=.5)]})
    data = samples(path)
    assert rms(data, .1, .7) < 1e-8
    assert rms(data, .8, 1.1) > .02
    assert rms(data, 1.3, 1.9) < 1e-8


@pytest.mark.parametrize('patch', [dict(start=-1), dict(end=.01), dict(speed=0), dict(at=-1),
                                    dict(at=float('nan')), dict(track=0), dict(track=True), dict(track=1.5),
                                    dict(loop='yes'), dict(loop=True, length=0), dict(at=7200), dict(volume=3)])
def test_invalid_audio_region_rejected(setup, media, patch):
    _, client, headers = setup
    video = upload(setup, media / 'red.mp4'); audio = upload(setup, media / 'music.wav', 'audio')
    region = dict(asset_id=audio['id'], start=0, end=.4, at=0, track=1, loop=False)
    region.update(patch)
    result = client.post('/api/editor/exports', headers=headers, json={
        'clips': [dict(asset_id=video['id'])], 'audio_clips': [region]})
    assert result.status_code == 400, result.json


def test_large_timeline_payload_and_audio_limits(setup, media):
    _, client, headers = setup
    video = upload(setup, media / 'red.mp4'); audio = upload(setup, media / 'music.wav', 'audio')
    body = {'clips': [dict(asset_id=video['id'], start=.012345678, end=.123456789, speed=1, volume=1)] * 50,
            'audio_clips': [dict(asset_id=audio['id'], start=0, end=.1, at=i / 10, speed=1, volume=.3,
                                 track=1, loop=False) for i in range(20)]}
    assert len(json.dumps(body)) > 8192
    result = client.post('/api/editor/exports', headers=headers, json=body)
    assert result.status_code == 202, result.json
    body['audio_clips'].append(body['audio_clips'][0])
    assert client.post('/api/editor/exports', headers=headers, json=body).status_code == 400
    assert client.post('/api/jobs', headers=headers, content_type='application/json', data=b'x' * 9000).status_code == 413

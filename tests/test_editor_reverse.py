"""Reverse actual media, checking frame order and per-channel decoded sample equality."""
import json
import subprocess

import pytest

from test_editor import setup, media, upload
from test_editor_quality import frame_hashes, audio_samples


@pytest.fixture
def changing_source(tmp_path):
    path = tmp_path / 'changing.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-filter_threads', '1',
                    '-f', 'lavfi', '-i', 'testsrc2=s=192x108:r=30:d=1',
                    '-f', 'lavfi', '-i', 'aevalsrc=0.1*sin(2*PI*330*t)|0.2*sin(2*PI*550*t):s=48000:d=1',
                    '-c:v', 'ffv1', '-pix_fmt', 'yuv420p10le', '-c:a', 'pcm_f64le', str(path)], check=True)
    return path


def run_export(editor, body):
    job = editor.enqueue(body); editor.render(job)
    directory = editor.exports / job['id']
    assert editor.job(job['id'])['status'] == 'complete', (editor.job(job['id']), (directory / 'ffmpeg.log').read_text())
    assert sorted(p.name for p in directory.iterdir()) == ['ffmpeg.log', 'result.mkv']
    return directory / 'result.mkv'


@pytest.mark.parametrize('speed', [.25, .5, 1, 2, 4])
def test_reversed_trimmed_video_frames_across_chunks(setup, changing_source, monkeypatch, speed):
    editor, _, _ = setup
    asset = upload(setup, changing_source)
    # Small chunks force six reverse passes rather than hiding boundary errors in a one-chunk test.
    monkeypatch.setattr('mediahub.editor.REVERSE_CHUNK_BYTES', 192 * 108 * 3 * 3)
    path = run_export(editor, {'clips': [dict(asset_id=asset['id'], start=.2, end=.8, speed=speed, reverse=True)]})
    assert frame_hashes(path) == frame_hashes(changing_source)[6:24][::-1]
    timestamps = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(path)]))['frames']
    points = [float(f['best_effort_timestamp_time']) for f in timestamps]
    assert points == sorted(points)
    assert points[0] == pytest.approx(0, abs=.002)
    assert points[-1] == pytest.approx((.6 - 1 / 30) / speed, abs=.04 / speed + .005)
    if speed == 1:
        original = audio_samples(changing_source)
        frames = [original[i:i + 16] for i in range(int(.2 * 48000) * 16, int(.8 * 48000) * 16, 16)]
        assert audio_samples(path) == b''.join(reversed(frames))


def test_independent_audio_reverses_before_loop_and_output_truncation(setup, changing_source):
    editor, _, _ = setup
    asset = upload(setup, changing_source)
    path = run_export(editor, {'clips': [dict(asset_id=asset['id'], volume=0)], 'audio_clips': [
        dict(asset_id=asset['id'], start=.2, end=.4, at=.1, reverse=True, loop=True, length=.5)]})
    expected = audio_samples(changing_source)[int(.2 * 48000) * 16:int(.4 * 48000) * 16]
    reversed_frames = b''.join(expected[i:i + 16] for i in range(len(expected) - 16, -1, -16))
    # The mix limiter is inactive at these amplitudes: compare stereo frames exactly.
    output = audio_samples(path)
    section = output[int(.1 * 48000) * 16:int(.6 * 48000) * 16]
    assert section == (reversed_frames * 3)[:len(section)]
    assert frame_hashes(path) == frame_hashes(changing_source)


@pytest.mark.parametrize('value', ['true', 1, None, []])
def test_bad_reverse_flags_rejected(setup, media, value):
    editor, client, headers = setup
    asset = upload(setup, media / 'red.mp4')
    for body in [dict(clips=[dict(asset_id=asset['id'], reverse=value)]),
                 dict(clips=[dict(asset_id=asset['id'])], audio_clips=[dict(asset_id=asset['id'], reverse=value)])]:
        response = client.post('/api/editor/exports', headers=headers, json=body)
        assert response.status_code == 400 and 'Reverse' in response.json['error']
    recipe = editor.validate(dict(clips=[dict(asset_id=asset['id'])]))
    assert recipe['clips'][0]['reverse'] is False


def test_reverse_sparse_variable_frame_rate(setup, changing_source, tmp_path, monkeypatch):
    editor, _, _ = setup
    sparse = tmp_path / 'sparse.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(changing_source), '-vf',
                    "select='lt(t,0.15)+gt(t,0.8)'", '-fps_mode', 'vfr', '-c:v', 'ffv1', '-an', str(sparse)], check=True)
    asset = upload(setup, sparse)
    monkeypatch.setattr('mediahub.editor.REVERSE_CHUNK_BYTES', 192 * 108 * 3 * 3)
    path = run_export(editor, {'clips': [dict(asset_id=asset['id'], reverse=True)]})
    assert frame_hashes(path) == frame_hashes(sparse)[::-1]


def test_reverse_instagram_copy(setup, media):
    editor, client, headers = setup
    asset = upload(setup, media / 'red.mp4')
    job = editor.enqueue({'profile': 'instagram', 'allow_lossy': True, 'shape': 'square',
                         'clips': [dict(asset_id=asset['id'], start=.2, end=.8, reverse=True, volume=0)]})
    editor.render(job)
    assert editor.job(job['id'])['status'] == 'complete'
    response = client.get(f"/api/editor/exports/{job['id']}/file")
    assert response.status_code == 200 and response.mimetype == 'video/mp4'


@pytest.mark.parametrize('speed', [1, 2])
def test_long_gop_reverse_keeps_all_frames_through_final_mux(setup, tmp_path, monkeypatch, speed):
    editor, _, _ = setup
    source = tmp_path / 'long-gop.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-filter_threads', '1', '-f', 'lavfi', '-i',
                    'testsrc2=s=320x180:r=30:d=4', '-f', 'lavfi', '-i', 'sine=r=48000:d=4',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '120', '-threads', '2', '-c:a', 'aac', str(source)], check=True)
    asset = upload(setup, source)
    monkeypatch.setattr('mediahub.editor.REVERSE_CHUNK_BYTES', 320 * 180 * 1.5 * 10)
    target = run_export(editor, dict(clips=[dict(asset_id=asset['id'], reverse=True, speed=speed)]))
    assert len(frame_hashes(source)) == 120
    assert frame_hashes(target) == frame_hashes(source)[::-1]
    frames = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(target)]))['frames']
    assert float(frames[-1]['best_effort_timestamp_time']) == pytest.approx((4 - 1 / 30) / speed, abs=.04)


def test_instagram_reverse_reaches_beginning_of_source_after_multiple_chunks(setup, tmp_path, monkeypatch):
    editor, _, _ = setup
    source = tmp_path / 'blue-red.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-filter_complex_threads', '1', '-f', 'lavfi', '-i',
                    'color=c=blue:s=320x180:r=30:d=2', '-f', 'lavfi', '-i', 'color=c=red:s=320x180:r=30:d=2',
                    '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '120', '-threads', '2', str(source)], check=True)
    asset = upload(setup, source)
    monkeypatch.setattr('mediahub.editor.REVERSE_CHUNK_BYTES', 320 * 180 * 1.5 * 10)
    job = editor.enqueue(dict(profile='instagram', allow_lossy=True, shape='square',
                              clips=[dict(asset_id=asset['id'], reverse=True)]))
    editor.render(job)
    assert editor.job(job['id'])['status'] == 'complete'
    target = editor.exports / job['id'] / 'result.mp4'
    for at, channel in [(.5, 0), (3.5, 2)]:
        pixel = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(target),
            '-vf', 'crop=2:2:iw/2:ih/2', '-frames:v', '1', '-pix_fmt', 'rgb24', '-threads', '1', '-f', 'rawvideo', '-'])
        assert pixel[channel] > 200

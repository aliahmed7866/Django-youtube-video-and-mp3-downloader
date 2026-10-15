"""Performance gates that also verify native-media accuracy."""
from collections import Counter
import subprocess
from pathlib import Path

import pytest

from test_editor import setup, media, upload
from test_editor_quality import audio_samples


def test_full_timeline_probes_each_source_kind_once_per_request(setup, media, monkeypatch):
    editor, _, _ = setup
    asset = upload(setup, media / 'red.mp4')
    from mediahub import editor as module
    original = module.probe
    calls = []
    def record(path, kind):
        calls.append((str(path), kind))
        return original(path, kind)
    monkeypatch.setattr(module, 'probe', record)
    body = dict(clips=[dict(asset_id=asset['id'], end=.1, reverse=True)] * 50,
                audio_clips=[dict(asset_id=asset['id'], end=.1, at=.1 * i, reverse=True) for i in range(20)])
    editor.enqueue(body)
    expected = Counter({(str(editor.asset_path(asset['id'])), 'video'): 1,
                        (str(editor.asset_path(asset['id'])), 'audio'): 1})
    assert Counter(calls) == expected
    # A later request must re-read media; the cache cannot hide on-disk changes across exports.
    editor.validate(body)
    assert Counter(calls) == expected + expected


def test_reverse_audio_crosses_disk_block_boundaries_without_channel_changes(setup, media, tmp_path):
    editor, _, _ = setup
    source = tmp_path / 'three-channels.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'aevalsrc=0.1*sin(2*PI*220*t)|0.2*sin(2*PI*330*t)|0.3*sin(2*PI*440*t):s=48000:d=2',
                    '-c:a', 'pcm_f64le', str(source)], check=True)
    audio = upload(setup, source, 'audio')
    video = upload(setup, media / 'red.mp4')
    job = editor.enqueue(dict(clips=[dict(asset_id=video['id'])]))
    directory = editor.exports / job['id']; directory.mkdir()
    args = editor.reverse_audio(job['id'], audio, 0, 2, directory, 'test', 0)
    actual = Path(args[-1]).read_bytes()
    original = audio_samples(source)
    width = 3 * 8
    assert len(original) > 2 * 1024**2
    assert actual == b''.join(original[i:i + width] for i in range(len(original) - width, -1, -width))

import importlib.util
import json
from pathlib import Path
import pytest
from yt_dlp import YoutubeDL
from mediahub.app import create_app, media_url
from mediahub.links import extract_shared_link
from mediahub.worker import command, friendly_error
from termux.share import destination


@pytest.mark.parametrize('path', ['reel/AbCd_12345', 'reels/AbCd_12345/', 'p/AbCd_12345/', 'creator/reel/AbCd_12345/', 'tv/AbCd_12345'])
def test_instagram_canonical_urls(path):
    assert media_url('https://www.instagram.com/'+path+'?igsh=tracking') == 'https://www.instagram.com/p/AbCd_12345/'


@pytest.mark.parametrize('url', ['https://www.instagram.com/creator/', 'https://www.instagram.com/stories/creator/12345/', 'https://www.instagram.com/reels/audio/12345/', 'https://instagram.com.evil.test/reel/AbCd123/', 'https://user@instagram.com/reel/AbCd123/', 'https://instagram.com:8080/reel/AbCd123/', 'https://instagram.com/share/reel/AbCd123/'])
def test_reject_unsupported_instagram_urls(url):
    with pytest.raises(ValueError): media_url(url)


def test_caption_extraction_rejects_ambiguous_or_untrusted_links():
    expected = 'https://www.instagram.com/p/AbCd_12345/'
    assert extract_shared_link('Watch this! https://www.instagram.com/reel/AbCd_12345/?igsh=x 😊', media_url) == expected
    assert extract_shared_link(expected+' '+expected, media_url) == expected
    for value in ['No link here', expected+' https://youtu.be/abcdefghijk', 'https://127.0.0.1/admin', 'x'*8001, None]:
        with pytest.raises(ValueError): extract_shared_link(value, media_url)


def test_share_prefills_without_queuing_and_escapes_html(tmp_path):
    app = create_app(tmp_path)
    client = app.test_client()
    response = client.get('/', query_string={'text':'A Reel https://instagram.com/reel/AbCd123/', 'title':'<script>alert(1)</script>'})
    assert response.status_code == 200
    assert b'value="https://www.instagram.com/p/AbCd123/"' in response.data
    assert b'<script>alert(1)</script>' not in response.data
    assert client.get('/api/jobs').json['total'] == 0
    bad = client.get('/', query_string={'url':'https://example.com/private'})
    assert b'not supported' in bad.data
    assert client.get('/api/jobs').json['total'] == 0


def test_manifest_and_worker(tmp_path):
    client = create_app(tmp_path).test_client()
    manifest = client.get('/manifest.webmanifest')
    assert manifest.mimetype == 'application/manifest+json'
    assert manifest.json['share_target']['method'] == 'GET'
    assert manifest.json['share_target']['action'] == '/'
    for icon in manifest.json['icons']:
        assert client.get(icon['src']).status_code == 200
    worker = client.get('/service-worker.js')
    assert worker.status_code == 200
    assert worker.headers['Service-Worker-Allowed'] == '/'
    assert b'caches.open' not in worker.data


def test_instagram_post_command_keeps_one_video_and_audio(tmp_path):
    args = command({'url':'https://www.instagram.com/p/AbCd123/', 'kind':'video', 'quality':'720'}, tmp_path)
    assert args[args.index('--playlist-items')+1] == '1'
    assert '[width<=720]' in args[args.index('-f')+1]
    with YoutubeDL({'allowed_extractors':args[args.index('--use-extractors')+1].split(','), 'quiet':True}) as downloader:
        assert set(downloader._ies) == {'Youtube', 'TikTok', 'TikTokVM', 'Instagram', 'Twitter'}
    audio = command({'url':'https://www.instagram.com/p/AbCd123/', 'kind':'audio', 'quality':'192'}, tmp_path)
    assert audio[audio.index('--audio-format')+1] == 'mp3'
    assert 'no downloadable video' in friendly_error('There is no video in this post')
    assert 'limiting access' in friendly_error('Instagram sent an empty media response')


def test_inline_playback_and_byte_range(tmp_path):
    app = create_app(tmp_path); client = app.test_client()
    store = app.extensions['store']; job = store.add('https://youtu.be/abcdefghijk', 'video', '720')
    folder = tmp_path / 'downloads' / job['id']; folder.mkdir(parents=True)
    (folder/'clip.mp4').write_bytes(b'0123456789')
    store.update(job['id'], status='complete', filename=f"downloads/{job['id']}/clip.mp4")
    url = f"/api/jobs/{job['id']}/file"
    assert client.get(url).headers['Content-Disposition'].startswith('attachment')
    response = client.get(url+'?play=1', headers={'Range':'bytes=2-5'})
    assert response.status_code == 206 and response.data == b'2345'
    assert response.headers['Content-Disposition'].startswith('inline')


def test_termux_share_reads_saved_port_without_executing_env(tmp_path):
    env = tmp_path/'env'
    env.write_text("export MEDIAHUB_PORT='8084'\nexport OTHER=$(touch should-not-exist)\n")
    url = destination('Look https://www.instagram.com/reel/AbCd123/', env)
    assert url.startswith('http://127.0.0.1:8084/?url=')
    assert 'instagram.com' in url
    env.write_text('export MEDIAHUB_PORT=1\n')
    with pytest.raises(ValueError): destination('https://youtu.be/abcdefghijk', env)


def test_share_installer_preserves_existing_handler(tmp_path):
    path = Path(__file__).parents[1]/'termux/install-share.py'
    spec = importlib.util.spec_from_file_location('install_share', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    target = tmp_path/'bin/termux-url-opener'
    module.install(target, tmp_path, '/termux/usr')
    module.install(target, tmp_path, '/termux/usr')
    assert target.stat().st_mode & 0o700 == 0o700
    target.write_text('existing handler')
    with pytest.raises(ValueError): module.install(target, tmp_path, '/termux/usr')
    assert target.read_text() == 'existing handler'


def test_instagram_api_uses_shared_caption_and_keeps_csrf(tmp_path):
    app = create_app(tmp_path); client = app.test_client(); client.get('/')
    with client.session_transaction() as session:
        headers = {'X-CSRF-Token': session['csrf']}
    data = {'url': 'Take a look https://instagram.com/reel/AbCd123/?igsh=tracking', 'kind': 'video', 'quality': '720'}
    assert client.post('/api/jobs', json=data).status_code == 403
    response = client.post('/api/jobs', json=data, headers=headers)
    assert response.status_code == 201
    assert response.json['url'] == 'https://www.instagram.com/p/AbCd123/'
    data['url'] = 'https://instagram.com/p/AbCd123/'
    assert client.post('/api/jobs', json=data, headers=headers).status_code == 400

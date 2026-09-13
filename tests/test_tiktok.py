from pathlib import Path
import pytest
from yt_dlp import YoutubeDL
from mediahub.app import create_app, media_url
from mediahub.worker import command


@pytest.mark.parametrize('raw,expected', [
    ('https://www.tiktok.com/@creator/video/1234567890123456789?tracking=1', 'https://www.tiktok.com/@creator/video/1234567890123456789'),
    ('http://m.tiktok.com/@creator.name/video/1234567890123456789/', 'https://www.tiktok.com/@creator.name/video/1234567890123456789'),
    ('https://vm.tiktok.com/ZMRAb123/?a=1', 'https://vm.tiktok.com/ZMRAb123/'),
    ('https://vt.tiktok.com/ZMRAb123/', 'https://vt.tiktok.com/ZMRAb123/'),
    ('https://www.tiktok.com/t/ZMRAb123/', 'https://www.tiktok.com/t/ZMRAb123/'),
    ('https://youtu.be/abcdefghijk', 'https://www.youtube.com/watch?v=abcdefghijk'),
])
def test_media_links(raw, expected):
    assert media_url(raw) == expected


@pytest.mark.parametrize('raw', [
    'https://tiktok.com/@creator', 'https://tiktok.com/@creator/live',
    'https://tiktok.com/@creator/photo/1234567890123456789',
    'https://tiktok.com.evil.test/@creator/video/1234567890123456789',
    'https://vm.tiktok.com/../admin', 'https://vm.tiktok.com/a/b',
    'https://user@vm.tiktok.com/abc/', 'https://vm.tiktok.com:8080/abc/',
    'file:///etc/passwd', None,
])
def test_rejected_links(raw):
    with pytest.raises(ValueError):
        media_url(raw)


def test_tiktok_api_and_duplicate_canonical_link(tmp_path):
    app = create_app(tmp_path)
    client = app.test_client(); client.get('/')
    with client.session_transaction() as session:
        headers = {'X-CSRF-Token': session['csrf']}
    url = 'https://www.tiktok.com/@creator/video/1234567890123456789'
    body = {'url':url+'?tracking=1', 'kind':'video', 'quality':'720'}
    assert client.post('/api/jobs', json=body, headers=headers).status_code == 201
    body['url'] = url
    assert client.post('/api/jobs', json=body, headers=headers).status_code == 400
    body['kind'] = 'audio'; body['quality'] = '192'
    assert client.post('/api/jobs', json=body, headers=headers).status_code == 201


def test_short_link_extractors_and_vertical_formats(tmp_path):
    args = command({'url':'https://vm.tiktok.com/ZMR123/', 'kind':'video', 'quality':'720'}, tmp_path)
    selected = args[args.index('--use-extractors')+1].split(',')
    with YoutubeDL({'allowed_extractors':selected, 'quiet':True}) as ydl:
        assert set(ydl._ies) == {'Youtube','TikTok','TikTokVM','Instagram'}
        selector = ydl.build_format_selector(args[args.index('-f')+1])
        formats = [{'format_id':'720','width':720,'height':1280,'ext':'mp4','vcodec':'h264','acodec':'aac','url':'https://example.com/video'}]
        result = list(selector({'formats':formats,'has_merged_format':True,'incomplete_formats':False}))
        assert result[0]['format_id'] == '720'

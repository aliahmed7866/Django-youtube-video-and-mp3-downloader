import pytest
from yt_dlp import YoutubeDL
from yt_dlp.extractor.twitter import TwitterIE
from mediahub.app import media_url, create_app
from mediahub.worker import command


@pytest.mark.parametrize('url', [
    'https://twitter.com/creator/status/1234567890123456789?s=20',
    'https://mobile.twitter.com/creator/status/1234567890123456789',
    'http://www.x.com/i/web/status/1234567890123456789/',
    'https://x.com/statuses/1234567890123456789',
])
def test_canonical_post(url):
    assert media_url(url) == 'https://x.com/i/web/status/1234567890123456789'


def test_specific_video_is_preserved():
    url = media_url('https://twitter.com/creator/status/1234567890123456789/video/2?s=20')
    assert url.endswith('/video/2')
    assert TwitterIE.suitable(url)
    assert TwitterIE._match_valid_url(url).group('index') == '2'


@pytest.mark.parametrize('url', [
    'https://x.com/creator', 'https://x.com/i/spaces/123',
    'https://t.co/abcd', 'https://x.com/creator/status/123/photo/1',
    'https://x.com/creator/status/123/video/0',
    'https://x.com/creator/status/123/video/2/extra',
    'https://x.com.evil.test/creator/status/123',
    'https://user@x.com/creator/status/123',
    'https://x.com:8080/creator/status/123',
])
def test_reject_other_urls(url):
    with pytest.raises(ValueError):
        media_url(url)


def test_shared_post_api_and_duplicate(tmp_path):
    client = create_app(tmp_path).test_client()
    client.get('/')
    with client.session_transaction() as session:
        headers = {'X-CSRF-Token': session['csrf']}
    data = {'url':'Watch https://twitter.com/creator/status/1234567890123456789?s=20', 'kind':'video','quality':'720'}
    assert client.post('/api/jobs', json=data).status_code == 403
    assert client.post('/api/jobs', json=data, headers=headers).status_code == 201
    data['url'] = 'https://x.com/i/web/status/1234567890123456789'
    assert client.post('/api/jobs', json=data, headers=headers).status_code == 400
    data['url'] += '/video/2'
    assert client.post('/api/jobs', json=data, headers=headers).status_code == 201


@pytest.mark.parametrize('layout', ['combined', 'split', 'silent'])
def test_real_x_format_selector(tmp_path, layout):
    url = media_url('https://x.com/creator/status/123/video/2')
    args = command({'url':url,'kind':'video','quality':'720'}, tmp_path)
    assert args[-1] == url
    assert args[args.index('--playlist-items')+1] == '1'
    formats = [{'format_id':'v','url':'https://example.com/v.mp4','ext':'mp4','vcodec':'h264','acodec':'aac' if layout == 'combined' else 'none','width':720,'height':1280}]
    if layout == 'split':
        formats.insert(0, {'format_id':'a','url':'https://example.com/a.m4a','ext':'m4a','vcodec':'none','acodec':'aac'})
    with YoutubeDL({'quiet':True,'allowed_extractors':args[args.index('--use-extractors')+1].split(',')}) as ydl:
        assert set(ydl._ies) == {'Youtube','TikTok','TikTokVM','Instagram','Twitter'}
        selector = ydl.build_format_selector(args[args.index('-f')+1])
        result = list(selector({'formats':formats,'has_merged_format':layout=='combined','incomplete_formats':False}))
        assert result[0]['format_id'] == ('v+a' if layout == 'split' else 'v')
    audio = command({'url':url,'kind':'audio','quality':'192'},tmp_path)
    assert audio[audio.index('--audio-format')+1] == 'mp3'

"""Use yt-dlp's real selector against Instagram's common stream layouts."""
from yt_dlp import YoutubeDL
import pytest
from mediahub.worker import command


def video(ident, width=720, height=1280, audio=False):
    result = {'format_id':ident, 'url':f'https://example.com/{ident}.mp4', 'ext':'mp4', 'vcodec':'h264', 'acodec':'aac' if audio else 'none'}
    if width is not None: result['width'] = width
    if height is not None: result['height'] = height
    return result


AUDIO = {'format_id':'audio', 'url':'https://example.com/audio.m4a', 'ext':'m4a', 'vcodec':'none', 'acodec':'aac'}


def select(tmp_path, formats, quality='720'):
    args = command({'url':'https://www.instagram.com/p/DdMeZEruCUC/', 'kind':'video', 'quality':quality}, tmp_path)
    with YoutubeDL({'quiet':True}) as downloader:
        selector = downloader.build_format_selector(args[args.index('-f')+1])
        return list(selector({'formats':formats, 'has_merged_format':any(f['acodec']!='none' and f['vcodec']!='none' for f in formats), 'incomplete_formats':False}))


def test_separate_video_and_audio_are_merged(tmp_path):
    result = select(tmp_path, [AUDIO, video('dash-video')])
    assert result[0]['format_id'] == 'dash-video+audio'
    assert len(result[0]['requested_formats']) == 2


def test_combined_stream_still_works(tmp_path):
    assert select(tmp_path, [video('combined', audio=True)])[0]['format_id'] == 'combined'


def test_unknown_dimensions_are_accepted(tmp_path):
    assert select(tmp_path, [AUDIO, video('unknown', None, None)])[0]['format_id'] == 'unknown+audio'
    assert select(tmp_path, [video('combined', None, None, True)])[0]['format_id'] == 'combined'


def test_known_dimensions_preferred_over_unknown(tmp_path):
    result = select(tmp_path, [AUDIO, video('known'), video('unknown', None, None)])
    assert result[0]['format_id'] == 'known+audio'


def test_higher_only_stream_needs_higher_quality(tmp_path):
    formats = [AUDIO, video('fullhd',1080,1920)]
    assert select(tmp_path, formats) == []
    assert select(tmp_path, formats, '1080')[0]['format_id'] == 'fullhd+audio'


def test_known_large_dimension_is_not_treated_as_unknown(tmp_path):
    assert select(tmp_path,[AUDIO,video('partial',1080,None)]) == []


def test_landscape_and_audio_only(tmp_path):
    assert select(tmp_path,[video('landscape',1280,720,True)])[0]['format_id'] == 'landscape'
    assert select(tmp_path,[AUDIO]) == []

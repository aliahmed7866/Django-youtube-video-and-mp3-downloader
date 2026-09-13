"""Extract a single supported URL from a phone share payload or pasted caption."""
import re


def extract_shared_link(value, validate):
    if not isinstance(value, str) or not value.strip() or len(value) > 8000:
        raise ValueError('Paste one YouTube, TikTok or Instagram video link.')
    candidates = re.findall(r'https?://[^\s<>"\x00-\x1f]+', value)
    if not candidates:
        raise ValueError('No video link found. Use Copy link or Share in the original app.')
    urls = set()
    for candidate in candidates:
        urls.add(validate(candidate.rstrip('.,!;:)\u3002')))
    if len(urls) != 1:
        raise ValueError('Share or paste one video at a time.')
    return urls.pop()

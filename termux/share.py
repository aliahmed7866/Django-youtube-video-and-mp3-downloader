"""Open a shared video in Media Hub for review; never submit downloads silently."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mediahub.app import media_url
from mediahub.links import extract_shared_link


def configured_port(env_file):
    port = '8083'
    for raw in env_file.read_text().splitlines():
        parts = shlex.split(raw, comments=True)
        if parts and parts[0] == 'export':
            parts = parts[1:]
        for part in parts:
            if part.startswith('MEDIAHUB_PORT='):
                port = part.split('=', 1)[1]
    value = int(port)
    if not 1024 <= value <= 65535:
        raise ValueError('Invalid Media Hub port in private configuration.')
    return value


def destination(shared, env_file):
    link = extract_shared_link(shared, media_url)
    return f'http://127.0.0.1:{configured_port(env_file)}/?' + urlencode({'url': link})


if __name__ == '__main__':
    config = Path(os.environ.get('MEDIAHUB_CONFIG_DIR', '~/.config/mediahub')).expanduser() / 'env'
    try:
        url = destination(' '.join(sys.argv[1:]), config)
        subprocess.run(['termux-open-url', url], check=True)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'Could not open Media Hub: {exc}')

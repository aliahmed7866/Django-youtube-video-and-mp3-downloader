"""Opt-in Android Share → Termux bridge. Keep any existing URL handler intact."""
import os
from pathlib import Path
import shlex


def install(destination, root, prefix):
    content = f'''#!{prefix}/bin/bash
# Media Hub share bridge
exec {shlex.quote(str(root / '.venv/bin/python'))} {shlex.quote(str(root / 'termux/share.py'))} "$@"
'''
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open('x') as file:
            file.write(content)
    except FileExistsError:
        if destination.is_symlink() or destination.read_text() != content:
            raise ValueError(f'{destination} already contains another share handler. It was preserved. Use Copy link and Paste in Media Hub, or integrate termux/share.py into your existing handler.')
    destination.chmod(0o700)


if __name__ == '__main__':
    try:
        install(Path.home() / 'bin/termux-url-opener', Path(__file__).resolve().parents[1], os.environ['PREFIX'])
    except (ValueError, KeyError) as exc:
        raise SystemExit(str(exc))
    print('Sharing enabled: choose Share → Termux, then choose your format in Media Hub.')

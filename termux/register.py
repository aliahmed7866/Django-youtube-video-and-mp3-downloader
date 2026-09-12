"""Merge Media Hub into the existing admin registry without losing apps."""
import json
import os
from pathlib import Path
import tempfile


def register(registry, example, port=8083):
    source = registry if registry.exists() else example
    payload = json.loads(source.read_text())
    if not isinstance(payload, dict) or not isinstance(payload.get('apps'), list):
        raise ValueError('Admin registry must contain an apps list; no changes made.')
    if any(x.get('port') == port and x.get('id') != 'mediahub' for x in payload['apps']):
        raise ValueError(f'Port {port} already belongs to another app. Set MEDIAHUB_PORT.')
    item = {'id':'mediahub','name':'Media Hub','description':'YouTube videos and MP3 audio, saved for offline',
            'service':'mediahub','port':port,'health_url':f'http://127.0.0.1:{port}/health','open_url':f'http://127.0.0.1:{port}'}
    payload['apps'] = [x for x in payload['apps'] if x.get('id') != 'mediahub'] + [item]
    registry.parent.mkdir(parents=True, exist_ok=True)
    if registry.exists():
        backup = registry.with_name(registry.name + '.before-mediahub')
        if not backup.exists():
            backup.write_bytes(registry.read_bytes())
            backup.chmod(0o600)
    with tempfile.NamedTemporaryFile(mode='w', dir=registry.parent, delete=False) as f:
        json.dump(payload, f, indent=2)
        f.write('\n')
    os.replace(f.name, registry)


if __name__ == '__main__':
    config = Path(os.environ.get('AYCF_CONFIG_DIR', '~/.config/aycf')).expanduser()
    registry = Path(os.environ.get('AYCF_ADMIN_REGISTRY', str(config / 'apps.json'))).expanduser()
    hub = Path(os.environ.get('AYCF_APP_DIR', '~/aycf-trip-planner')).expanduser()
    register(registry, hub / 'termux/apps.json.example', int(os.environ.get('MEDIAHUB_PORT', '8083')))

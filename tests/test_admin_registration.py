import importlib.util
import json
from pathlib import Path
import subprocess
from termux.register import register


def test_registration_matches_service_without_matching_worker(tmp_path):
    registry = tmp_path / 'apps.json'
    registry.write_text(json.dumps({'apps': [{'id': 'mediahub', 'name': 'Media Hub', 'actions': [{'id': 'custom'}], 'port': 8084}]}))
    register(registry, tmp_path / 'unused', 8084)
    register(registry, tmp_path / 'unused', 8084)
    apps = json.loads(registry.read_text())['apps']
    assert len(apps) == 1
    app = apps[0]
    root = Path(__file__).resolve().parents[1]
    assert app['working_dir'] == str(root)
    assert app['actions'] == [{'id': 'custom'}]
    assert app['health_url'] == 'http://127.0.0.1:8084/health'
    assert app['install_command'] == ['bash', str(root / 'termux/install-service.sh')]
    for command, matches in [(f'{root}/.venv/bin/python run.py', True),
                              (f'{root}/.venv/bin/python termux/deploy.py', False),
                              (f'{root}/.venv/bin/python -m yt_dlp URL', False),
                              ('/another/app/.venv/bin/python run.py', False)]:
        result = subprocess.run(['grep', '-E', app['process_match']], input=command+'\n', text=True, capture_output=True)
        assert (result.returncode == 0) is matches

"""Exercise the real shell installer with delayed fake Termux supervision."""
import os
from pathlib import Path
import shutil
import subprocess


def test_installer_waits_for_both_new_services(tmp_path):
    root = tmp_path / 'app'; (root/'termux').mkdir(parents=True)
    script = Path(__file__).parents[1]/'termux/install-service.sh'
    shutil.copy(script, root/'termux/install-service.sh')
    (root/'.venv/bin').mkdir(parents=True)
    prefix = tmp_path/'usr'; binary = prefix/'bin'; binary.mkdir(parents=True)
    profile = prefix/'etc/profile.d'; profile.mkdir(parents=True)
    home = tmp_path/'home'; home.mkdir()
    def executable(path, content):
        path.write_text('#!/bin/bash\n'+content+'\n'); path.chmod(0o755)
    executable(root/'.venv/bin/python', 'exit 0')
    executable(binary/'pkg', 'exit 0')
    executable(binary/'ffmpeg', 'exit 0')
    executable(binary/'sv-enable', 'test -p "$SVDIR/$1/supervise/ok"')
    executable(binary/'sv', 'test -p "${@: -1}/supervise/ok"')
    profile.joinpath('start-services.sh').write_text('''
(
  for n in {1..80}; do
    for service in mediahub mediahub-deploy; do
      if [ -d "$SVDIR/$service" ] && [ ! -p "$SVDIR/$service/supervise/ok" ]; then
        mkdir -p "$SVDIR/$service/supervise"
        mkfifo "$SVDIR/$service/supervise/ok"
      fi
    done
    sleep 0.05
  done
) >/dev/null 2>&1 &
''')
    result = subprocess.run(['bash',str(root/'termux/install-service.sh')], env={**os.environ, 'PREFIX':str(prefix), 'HOME':str(home), 'PATH':str(binary)+':'+os.environ['PATH']}, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    for service in ('mediahub','mediahub-deploy'):
        assert prefix.joinpath('var/service',service,'run').exists()

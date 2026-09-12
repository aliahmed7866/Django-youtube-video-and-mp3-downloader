"""Runit watcher: fast-forward master updates, report status, retry failures."""
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get('MEDIAHUB_STATE_DIR', '~/.local/state/mediahub')).expanduser()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True, timeout=90).strip()


def deploy():
    if git('branch', '--show-current') != 'master':
        return 'Skipped: checkout is not on master.'
    if git('status', '--porcelain', '--untracked-files=no'):
        return 'Skipped: local tracked files have changes.'
    git('fetch', 'origin', 'master')
    target = git('rev-parse', 'origin/master')
    stamp = STATE / 'last-successful-sha'
    if stamp.exists() and stamp.read_text().strip() == target and git('rev-parse', 'HEAD') == target:
        return f'Up to date: {target[:12]}'
    git('merge', '--ff-only', 'origin/master')
    # The installer exits nonzero unless the application responds healthy.
    subprocess.run(['bash', 'termux/install-service.sh'], cwd=ROOT, check=True, timeout=900,
                   env={**os.environ, 'MEDIAHUB_FROM_DEPLOY':'1'})
    stamp.write_text(target + '\n')
    return f'Deployed successfully: {target[:12]}'


if __name__ == '__main__':
    STATE.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            status = deploy()
        except Exception as exc:
            status = f'Deployment failed; will retry: {exc}'
        (STATE / 'deploy-status.txt').write_text(time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())+'\n'+status+'\n')
        print(status, flush=True)
        time.sleep(300)

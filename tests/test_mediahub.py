import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from mediahub.app import create_app, youtube_url
from mediahub.store import Store
from mediahub.worker import Worker, command
from termux.register import register


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path)
    app.testing = True
    client = app.test_client()
    client.get('/')
    with client.session_transaction() as session:
        headers = {'X-CSRF-Token':session['csrf']}
    return app, client, headers


@pytest.mark.parametrize('url', ['https://youtu.be/abcdefghijk?t=3', 'https://www.youtube.com/watch?v=abcdefghijk&list=abc', 'https://youtube.com/shorts/abcdefghijk', 'https://music.youtube.com/watch?v=abcdefghijk'])
def test_canonical_urls(url):
    assert youtube_url(url) == 'https://www.youtube.com/watch?v=abcdefghijk'


@pytest.mark.parametrize('url', ['file:///etc/passwd','http://127.0.0.1:8079','https://youtube.com.evil.test/watch?v=abcdefghijk','https://evil@youtube.com/watch?v=abcdefghijk','https://youtube.com/playlist?list=foo',None,'https://youtu.be/bad','https://youtube.com:bad/watch?v=abcdefghijk'])
def test_reject_urls(url):
    with pytest.raises(ValueError):
        youtube_url(url)


def test_api_lifecycle(setup):
    app, client, headers = setup
    assert client.post('/api/jobs',json={}).status_code == 403
    assert client.get('/',headers={'Host':'evil.test'}).status_code == 403
    assert client.post('/api/jobs',json=[],headers=headers).status_code == 400
    assert client.post('/api/jobs',json={'url':'https://youtu.be/abcdefghijk','quality':'999'},headers=headers).status_code == 400
    response = client.post('/api/jobs',json={'url':'https://youtu.be/abcdefghijk','quality':'720'},headers=headers)
    assert response.status_code == 201
    ident=response.json['id']
    assert client.get(f'/api/jobs/{ident}/file').status_code == 404
    assert client.delete(f'/api/jobs/{ident}',headers=headers).status_code == 409
    assert client.post(f'/api/jobs/{ident}/cancel',headers=headers).json['status']=='cancelled'
    retry=client.post(f'/api/jobs/{ident}/retry',headers=headers)
    assert retry.status_code==201 and retry.json['id']!=ident
    assert client.delete(f'/api/jobs/{ident}',headers=headers).status_code==204


def test_file_isolation(setup):
    app,client,headers=setup
    store=app.extensions['store'];job=store.add('x','audio','192')
    directory=store.root/'downloads'/job['id'];directory.mkdir(parents=True)
    (directory/'track.mp3').write_bytes(b'media')
    store.update(job['id'],status='complete',filename=f"downloads/{job['id']}/track.mp3")
    assert client.get(f"/api/jobs/{job['id']}/file").data==b'media'
    store.update(job['id'],filename='secret')
    assert client.get(f"/api/jobs/{job['id']}/file").status_code==404


def test_recovery_and_capacity(tmp_path):
    store=Store(tmp_path);job=store.add('x','video','720');store.claim()
    queued=store.add('y','video','720');store.recover()
    assert store.get(job['id'])['status']=='failed'
    assert store.get(queued['id'])['status']=='queued'
    for i in range(19):store.add(str(i),'video','720')
    with pytest.raises(ValueError):store.add('x','video','720')


def test_atomic_claim(tmp_path):
    store=Store(tmp_path);store.add('x','video','720')
    claimed=[]
    threads=[threading.Thread(target=lambda:claimed.append(store.claim())) for _ in range(4)]
    for thread in threads:thread.start()
    for thread in threads:thread.join()
    assert sum(x is not None for x in claimed)==1


def test_registry_preserves_apps(tmp_path):
    registry=tmp_path/'apps.json';registry.write_text(json.dumps({'custom':42,'apps':[{'id':'aycf','port':8080,'name':'AYCF'}]}))
    register(registry,tmp_path/'missing');register(registry,tmp_path/'missing')
    result=json.loads(registry.read_text())
    assert result['custom']==42 and len(result['apps'])==2
    assert result['apps'][0]['id']=='aycf'
    assert registry.with_name('apps.json.before-mediahub').exists()
    with pytest.raises(ValueError):register(registry,tmp_path/'missing',8080)


def test_worker_real_subprocess(tmp_path,monkeypatch):
    store=Store(tmp_path);job=store.add('x','audio','192');store.claim()
    def fake_command(job,directory):
        output=directory/'song.mp3'
        script=f"from pathlib import Path; import json; Path({str(output)!r}).write_bytes(b'media'); print('PROGRESS:50%'); print('RESULT:'+json.dumps({{'title':'My song','filepath':{str(output)!r}}}))"
        return [sys.executable,'-c',script]
    monkeypatch.setattr('mediahub.worker.command',fake_command)
    monkeypatch.setattr('mediahub.worker.shutil.which',lambda _: '/fake/ffmpeg')
    Worker(store).download(job)
    result=store.get(job['id'])
    assert result['status']=='complete' and result['title']=='My song'
    assert (store.root/result['filename']).read_bytes()==b'media'


def test_worker_cancel_terminates_process(tmp_path,monkeypatch):
    store=Store(tmp_path);job=store.add('x','video','720');store.claim()
    monkeypatch.setattr('mediahub.worker.command',lambda *args:[sys.executable,'-c','import time; time.sleep(30)'])
    monkeypatch.setattr('mediahub.worker.shutil.which',lambda _: '/fake/ffmpeg')
    worker=Worker(store);thread=threading.Thread(target=worker.download,args=(job,));thread.start()
    time.sleep(.15);store.update(job['id'],status='cancelling');thread.join(timeout=5)
    assert not thread.is_alive()
    assert store.get(job['id'])['status']=='cancelled'


def test_command_formats(tmp_path):
    args=command({'url':'https://youtu.be/abcdefghijk','kind':'audio','quality':'192'},tmp_path)
    assert args[args.index('--audio-quality')+1]=='192K'
    assert '--no-playlist' in args and args[-2]=='--'
    args=command({'url':'x','kind':'video','quality':'1080'},tmp_path)
    assert '[height<=1080]' in args[args.index('-f')+1]


def test_health_reports_worker(setup):
    app,client,_=setup
    assert client.get('/health').status_code==503
    app.extensions['worker']=SimpleNamespace(thread=SimpleNamespace(is_alive=lambda:True))
    assert client.get('/health').json['ok'] is True


def test_queue_pause_persists_and_does_not_interrupt_active_job(setup):
    app, client, headers = setup
    store = app.extensions['store']
    current = store.add('one', 'video', '720')
    store.claim()
    waiting = store.add('two', 'audio', '192')
    assert client.post('/api/queue', json={'paused': True}).status_code == 403
    assert client.post('/api/queue', json={'paused': 'true'}, headers=headers).status_code == 400
    assert client.post('/api/queue', json={'paused': True}, headers=headers).json['paused']
    reopened = Store(store.root)
    assert reopened.claim() is None
    assert reopened.get(current['id'])['status'] == 'downloading'
    client.post('/api/queue', json={'paused': False}, headers=headers)
    assert reopened.claim()['id'] == waiting['id']


def test_duplicate_queue_requests_are_atomic(tmp_path):
    store = Store(tmp_path)
    accepted = []
    def add():
        try:
            accepted.append(store.add('same', 'audio', '192'))
        except ValueError:
            pass
    threads = [threading.Thread(target=add) for _ in range(5)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert len(accepted) == 1
    store.update(accepted[0]['id'], status='failed')
    assert store.add('same', 'audio', '192')['id'] != accepted[0]['id']
    assert store.add('same', 'audio', '320')


def test_active_jobs_remain_visible_and_history_expands(setup):
    app, client, _ = setup
    store = app.extensions['store']
    waiting = store.add('waiting', 'video', '720')
    for i in range(210):
        job = store.add(str(i), 'audio', '192')
        store.update(job['id'], status='complete')
    page = client.get('/api/jobs').json
    assert page['total'] == 211 and len(page['jobs']) == 200
    assert page['jobs'][0]['id'] == waiting['id']
    assert len(client.get('/api/jobs?limit=400').json['jobs']) == 211
    for value in ['bad', '-1', '5001']:
        assert client.get('/api/jobs?limit=' + value).status_code == 400


def test_friendly_error_messages():
    from mediahub.worker import friendly_error
    assert 'account verification' in friendly_error('ERROR: Sign in to confirm you are not a bot')
    assert 'connection' in friendly_error('SSL certificate failed')
    assert 'space' in friendly_error('No space left on device')
    assert 'lower quality' in friendly_error('Requested format is not available')

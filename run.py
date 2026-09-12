import fcntl
import os
import signal
from waitress import serve
from mediahub.app import create_app
from mediahub.worker import Worker

if __name__ == '__main__':
    app = create_app()
    lock = (app.extensions['store'].root / 'service.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    worker = Worker(app.extensions['store'])
    app.extensions['worker'] = worker
    worker.start()
    def shutdown(signum, frame):
        worker.stop.set()
        worker.thread.join(timeout=8)
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    serve(app, host='127.0.0.1', port=int(os.environ.get('MEDIAHUB_PORT', '8083')), threads=4)

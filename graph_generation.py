"""Cancellation and complete-file publication for a PHP-owned graph generator."""

from contextlib import contextmanager
import os
from pathlib import Path
import signal
import stat
import sys
import threading
import time
import uuid


class GenerationControl:
    def __init__(self, cancel_fd=None):
        self.cancelled = threading.Event()
        self._connection = None
        self._lock = threading.Lock()
        if cancel_fd is not None:
            signal.signal(signal.SIGTERM, self._stop)
            threading.Thread(target=self._watch, args=(cancel_fd,), daemon=True).start()

    @staticmethod
    def _stop(signum, frame):
        raise SystemExit('Graph generation cancelled.')

    def check(self):
        if self.cancelled.is_set():
            raise SystemExit('Graph generation cancelled.')

    def _watch(self, descriptor):
        # A cancellation message or loss of the owning PHP process both stop this job.
        os.read(descriptor, 1)
        self.cancelled.set()
        while True:
            with self._lock:
                if self._connection is not None:
                    try:
                        # Psycopg supports cancel() from another thread while read_sql blocks.
                        self._connection.cancel()
                    except Exception as error:
                        print(f'Unable to cancel graph SQL operation: {error}', file=sys.stderr, flush=True)
            os.kill(os.getpid(), signal.SIGTERM)
            # Cover a cancellation racing with the start of a database operation.
            time.sleep(0.1)

    @contextmanager
    def connection(self, factory):
        self.check()
        connection = factory()
        try:
            with self._lock:
                self.check()
                self._connection = connection
            with connection:
                self.check()
                yield connection
        finally:
            with self._lock:
                self._connection = None
                connection.close()

    @contextmanager
    def output(self, destination):
        # Cancellation during CSV writes must not truncate a previously complete file.
        destination = Path(destination)
        temporary = destination.with_name('.' + destination.name + '.' + uuid.uuid4().hex + '.tmp')
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        os.close(descriptor)
        try:
            if destination.exists():
                temporary.chmod(stat.S_IMODE(destination.stat().st_mode))
            self.check()
            yield temporary
            self.check()
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

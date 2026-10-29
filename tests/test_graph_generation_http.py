"""Exercise real HTTP requests against a bounded PHP server and synthetic jobs."""

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
import signal
import socket
import subprocess
import time
import unittest
from urllib.parse import urlencode

import test_graph_generation_queue as fixtures


@unittest.skipUnless(fixtures.HAS_SEMAPHORES and os.name == "posix", "PHP with sysvsem and POSIX workers is required")
class GraphGenerationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GraphGenerationQueueTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        # The PHP development server uses plain local HTTP. Simulate the HTTPS
        # host seen by the application's origin checks; all settings are invented.
        (self.root / "router.php").write_text('''<?php
$_SERVER['HTTPS'] = 'on';
$_SERVER['HTTP_HOST'] = 'viewer.fixture.invalid';
if (parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH) === '/health') {
    header('Content-Type: text/plain');
    echo 'ready';
    return;
}
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
if (in_array($path, ['/call-pyscript.php', '/cancel-graph-generation.php'], true)) {
    require __DIR__ . $path;
} else {
    http_response_code(404);
}
''')
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            self.port = reserved.getsockname()[1]
        environment = dict(self.fixture.environment, PHP_CLI_SERVER_WORKERS="4")
        self.log = (self.root / "http.log").open("w+")
        self.addCleanup(self.log.close)
        self.server = subprocess.Popen(
            [fixtures.PHP, "-d", "display_errors=0", "-S", "127.0.0.1:" + str(self.port),
             "-t", str(self.root), str(self.root / "router.php")],
            cwd=self.root, env=environment, stdout=self.log, stderr=self.log,
            start_new_session=True,
        )
        self.addCleanup(self.stop_server)
        deadline = time.monotonic() + 5
        while True:
            try:
                if self.http("/health", timeout=0.2)[0] == 200:
                    break
            except (OSError, http.client.HTTPException):
                pass
            self.assertIsNone(self.server.poll(), self.log_text())
            self.assertLess(time.monotonic(), deadline, self.log_text())
            time.sleep(0.02)

    def log_text(self):
        return (self.root / "http.log").read_text()

    def stop_server(self):
        # Every process in this session belongs to this temporary HTTP fixture.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.server.pid, sig)
            except ProcessLookupError:
                break
            try:
                self.server.wait(timeout=1)
            except subprocess.TimeoutExpired:
                continue
            break

    def http(self, path, method="GET", timeout=3):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            connection.request(method, path, headers={
                "Origin": "https://viewer.fixture.invalid", "Sec-Fetch-Site": "same-origin",
            })
            response = connection.getresponse()
            headers = {}
            for name, value in response.getheaders():
                headers[name] = headers[name] + ', ' + value if name in headers else value
            return response.status, headers, response.read().decode()
        finally:
            connection.close()

    def graph_url(self, graph, generation, debug=False):
        return "/call-pyscript.php?" + urlencode({
            "filename": "panel", "graphno": graph, "generation": generation,
            **({"debug": "1"} if debug else {}),
        })

    def test_repeated_busy_panels_leave_http_workers_available_for_cancellation(self):
        # More simultaneous graph requests than HTTP workers reproduces the
        # original deadlock if waiting requests hold their worker open.
        for cycle in range(3):
            with self.subTest(cycle=cycle):
                generation = self.fixture.token()
                (self.root / "behavior.json").write_text('{"delay": 10}')
                (self.root / "events.jsonl").unlink(missing_ok=True)
                # Six client connections also model an HTTP/1 browser pool.
                pool = ThreadPoolExecutor(max_workers=6)
                active = [pool.submit(self.http, self.graph_url(i, generation), timeout=8) for i in (1, 2)]
                try:
                    self.fixture.wait_for(lambda: len([e for e in self.fixture.events() if e["event"] == "started"]) == 2)
                    waiting = [pool.submit(self.http, self.graph_url(i, generation)) for i in range(3, 19)]
                    health = pool.submit(self.http, "/health")
                    started = time.monotonic()
                    cancellation = pool.submit(self.http, "/cancel-graph-generation.php", "POST")
                    busy_responses = 0
                    for pending in waiting:
                        status, headers, body = pending.result(timeout=4)
                        self.assertIn(status, (202, 409))
                        if status == 202:
                            busy_responses += 1
                            self.assertEqual(headers.get("X-Graph-Generation"), "waiting")
                            self.assertEqual(headers.get("Retry-After"), "2")
                            self.assertIn("Waiting for a graph generation slot", body)
                        else:
                            # Cancellation can overtake the final few HTTP attempts.
                            self.assertEqual(headers.get("X-Graph-Generation"), "cancelled")
                        self.assertIn("no-store", headers.get("Cache-Control", ""))
                    self.assertGreater(busy_responses, 0)
                    self.assertEqual(health.result(timeout=3)[0], 200)
                    status, _, body = cancellation.result(timeout=3)
                    self.assertEqual(status, 200)
                    self.assertTrue(json.loads(body)["ok"])
                    self.assertLess(time.monotonic() - started, 1)
                    for running in active:
                        status, headers, _ = running.result(timeout=6)
                        self.assertEqual(status, 409)
                        self.assertEqual(headers.get("X-Graph-Generation"), "cancelled")
                    self.assertEqual(self.http(self.graph_url(3, generation))[0], 409)
                    self.assertEqual(len([e for e in self.fixture.events() if e["event"] == "started"]), 2)
                    self.assertEqual(self.fixture.peak_generators(), 2)
                finally:
                    # Out-of-band cleanup also stops jobs if a regression makes
                    # the HTTP cancellation endpoint unreachable.
                    self.fixture.cancel()
                    pool.shutdown(wait=True)
        (self.root / "behavior.json").write_text('{"delay": 0.05}')
        status, _, csv = self.http(self.graph_url(1, self.fixture.token()))
        self.assertEqual(status, 200)
        self.assertEqual(csv, "time,value\n2026-01-01T00:00:00,1\n")

    def test_query_document_retries_with_its_original_cancellation_token(self):
        self.fixture.configure(limit=1)
        generation = self.fixture.token()
        (self.root / "behavior.json").write_text('{"delay": 10}')
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(self.http, self.graph_url(1, generation), timeout=8)
            try:
                self.fixture.wait_for(lambda: bool(self.fixture.events()))
                # A bookmarked query may initially have no token; its retry must acquire one.
                status, headers, html = self.http("/call-pyscript.php?filename=panel&graphno=2&debug=1")
                self.assertEqual(status, 202)
                self.assertIn("text/html", headers.get("Content-Type", ""))
                self.assertIn("location.replace", html)
                self.assertIn("generation=" + generation, html)
                self.assertEqual(self.http("/cancel-graph-generation.php", "POST")[0], 200)
                self.assertEqual(self.http(self.graph_url(2, generation, debug=True))[0], 409)
                self.assertEqual(running.result(timeout=6)[0], 409)
            finally:
                self.fixture.cancel()

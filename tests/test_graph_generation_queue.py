"""Run real PHP workers against a synthetic generator; never load DB credentials."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
PHP = shutil.which("php")
HAS_SEMAPHORES = PHP and subprocess.run(
    [PHP, "-r", "exit(function_exists('sem_get') ? 0 : 1);"],
    capture_output=True, timeout=10,
).returncode == 0
HARNESS = r"""
$request = json_decode(stream_get_contents(STDIN), true);
$_SERVER = $request['server'];
$_GET = $request['get'];
$_POST = $request['post'] ?? [];
register_shutdown_function(function () {
    fwrite(STDERR, "\nQUEUE_TEST_STATUS=" . (http_response_code() ?: 200) . "\n");
});
require $argv[1];
"""
GENERATOR = r'''
import fcntl
import json
from pathlib import Path
import sys
import time
import os
import signal
from graph_generation import GenerationControl

root = Path(__file__).parent
panel = Path(sys.argv[1])
graph = sys.argv[2]
options = json.loads((root / "behavior.json").read_text())
if options.get("ignore_cancellation"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    control = GenerationControl()
else:
    control = GenerationControl(int(sys.argv[sys.argv.index("--cancel-fd") + 1]))

def record(event):
    with (root / "events.jsonl").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps({"event": event, "graph": graph, "pid": os.getpid()}) + "\n")

class FakeConnection:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cancel(self): record("sql_cancelled")
    def close(self): pass

record("started")
try:
    with control.connection(FakeConnection):
        time.sleep(options.get("delay", 0.25))
    if graph in options.get("fail", []):
        raise SystemExit("Synthetic generation failure")
    directory = root / "csv-files" / panel.parent / panel.stem
    directory.mkdir(parents=True, exist_ok=True)
    with control.output(directory / (panel.stem + "-" + graph + ".csv")) as output:
        output.write_text("time,value\n2026-01-01T00:00:00," + graph + "\n")
        if options.get("write_delay"):
            record("writing")
            time.sleep(options["write_delay"])
    print("Synthetic generator finished")
finally:
    record("finished")
'''


@unittest.skipUnless(HAS_SEMAPHORES, "PHP CLI with sysvsem is required")
class GraphGenerationQueueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ida-graph-queue-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name in ("call-pyscript.php", "viewer-config.php", "data-paths.php",
                     "graph-generation.php", "cancel-graph-generation.php", "graph_generation.py"):
            shutil.copyfile(REPO / name, self.root / name)
        self.addCleanup(self.remove_semaphore)
        self.addCleanup(self.remove_cancellation_state)
        (self.root / "json-files").mkdir()
        (self.root / "json-files/panel").write_text('{"graphs": []}')
        (self.root / "csv-files").mkdir()
        (self.root / "generate-graph-data.py").write_text(GENERATOR)
        (self.root / "behavior.json").write_text('{"delay": 0.25}')
        (self.root / "bin").mkdir()
        shim = self.root / "bin/python3"
        shim.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
        shim.chmod(0o700)
        self.environment = dict(os.environ, PATH=str(shim.parent) + os.pathsep + os.environ["PATH"])
        self.configure()

    def remove_semaphore(self):
        # The key includes this temporary checkout's path; no live semaphore is touched.
        subprocess.run(
            [PHP, "-r", "$key = hexdec(substr(hash('sha256', 'ida-graph-generators:' . $argv[1]), 0, 7)) + 1; "
             "$slot = sem_get($key, 2, 0600, true); if ($slot !== false) { sem_remove($slot); }",
             str(self.root)], capture_output=True, timeout=10,
        )

    def remove_cancellation_state(self):
        # Only this temporary checkout's private cancellation state is removed.
        subprocess.run(
            [PHP, "-r", "require $argv[1]; $state = graph_generation_state(); "
             "$path = stream_get_meta_data($state)['uri']; fclose($state); unlink($path); rmdir(dirname($path));",
             str(self.root / "graph-generation.php")], capture_output=True, timeout=10,
        )

    def token(self):
        result = subprocess.run(
            [PHP, "-r", "require $argv[1]; echo graph_generation_token();",
             str(self.root / "graph-generation.php")], capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"^[0-9a-f]{32}$")
        return result.stdout

    def cancel(self, **kwargs):
        return self.request(endpoint="cancel-graph-generation.php", method="POST", **kwargs)

    def wait_for(self, condition):
        deadline = time.monotonic() + 5
        while not condition():
            self.assertLess(time.monotonic(), deadline, self.events())
            time.sleep(0.01)

    def configure(self, limit=None, read_only=False):
        (self.root / "config.ini").write_text(
            '[viewer]\nread_only = ' + ("true" if read_only else "false")
            + '\ndisplay_timezone = "UTC"\n'
            + ("" if limit is None else "max_graph_generators = " + str(limit) + "\n")
        )

    def request(self, graph=1, debug=False, site="same-origin", disabled="", generation=None,
                endpoint="call-pyscript.php", method="GET", origin="https://viewer.fixture.invalid"):
        result = subprocess.run(
            [PHP, "-d", "display_errors=0", "-d", "disable_functions=" + disabled,
             "-r", HARNESS, str(self.root / endpoint)],
            input=json.dumps({
                "server": {"REQUEST_METHOD": method, "HTTP_HOST": "viewer.fixture.invalid",
                           "HTTPS": "on", "SCRIPT_NAME": "/data/call-pyscript.php",
                           "REMOTE_USER": "fixture-user-" + str(int(graph) % 2),
                           "HTTP_SEC_FETCH_SITE": site, "HTTP_ORIGIN": origin},
                "get": {"filename": "panel", "graphno": str(graph), **({"debug": "1"} if debug else {}),
                        **({"generation": generation} if generation is not None else {})},
            }), text=True, capture_output=True, env=self.environment, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        match = re.search(r"QUEUE_TEST_STATUS=(\d+)", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        return int(match[1]), result.stdout

    def load_graph(self, *args, **kwargs):
        # Model browser retries, keeping the page's original cancellation token.
        if kwargs.get("generation") is None:
            kwargs["generation"] = self.token()
        deadline = time.monotonic() + 12
        while True:
            status, body = self.request(*args, **kwargs)
            if status != 202:
                return status, body
            self.assertLess(time.monotonic(), deadline, "Graph stayed busy after its slot should have been freed")
            time.sleep(0.05)

    def events(self):
        path = self.root / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def peak_generators(self):
        active = peak = 0
        for event in self.events():
            active += {"started": 1, "finished": -1}.get(event["event"], 0)
            self.assertGreaterEqual(active, 0)
            peak = max(peak, active)
        self.assertEqual(active, 0, self.events())
        return peak

    def parallel_requests(self, count=6, debug=False):
        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(lambda graph: self.load_graph(graph, debug=debug), range(1, count + 1)))

    def test_default_allows_four_generators_across_independent_php_workers(self):
        responses = self.parallel_requests()
        self.assertEqual([status for status, _ in responses], [200] * 6)
        for graph, (_, csv) in enumerate(responses, 1):
            self.assertEqual(csv, "time,value\n2026-01-01T00:00:00," + str(graph) + "\n")
        self.assertEqual(self.peak_generators(), 4)
        self.assertFalse((self.root / "psql_credentials.py").exists())

    def test_limit_one_serialises_generation(self):
        self.configure(limit=1)
        self.assertEqual([status for status, _ in self.parallel_requests(4)], [200] * 4)
        self.assertEqual(self.peak_generators(), 1)

    def test_configured_limit_three_also_applies_to_query_links(self):
        self.configure(limit=3)
        responses = self.parallel_requests(debug=True)
        self.assertEqual([status for status, _ in responses], [200] * 6)
        self.assertTrue(all("Synthetic generator finished" in body for _, body in responses))
        self.assertEqual(self.peak_generators(), 3)

    def test_nonzero_generator_exit_releases_slot_for_waiting_requests(self):
        self.configure(limit=1)
        (self.root / "behavior.json").write_text('{"delay": 0.1, "fail": ["2"]}')
        responses = self.parallel_requests(4)
        self.assertEqual([status for status, _ in responses], [200, 500, 200, 200])
        self.assertIn("Graph data could not be generated", responses[1][1])
        self.assertEqual(self.peak_generators(), 1)

    def test_process_start_exception_also_releases_slot(self):
        self.configure(limit=1)
        status, _ = self.request(disabled="proc_open")
        self.assertEqual(status, 500)
        self.assertEqual(self.events(), [])
        self.assertEqual(self.request()[0], 200)

    def test_busy_slot_returns_promptly_without_starting_another_generator(self):
        self.configure(limit=1)
        generation = self.token()
        (self.root / "behavior.json").write_text('{"delay": 10}')
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(self.load_graph, generation=generation)
            try:
                self.wait_for(lambda: bool(self.events()))
                started = time.monotonic()
                status, body = self.request(2, generation=generation)
                self.assertEqual(status, 202)
                self.assertIn("Waiting for a graph generation slot", body)
                self.assertLess(time.monotonic() - started, 1)
                self.assertEqual(len([e for e in self.events() if e["event"] == "started"]), 1)
                status, body = self.request(3, debug=True)
                self.assertEqual(status, 202)
                self.assertIn("This page will retry automatically", body)
                self.assertIn("location.replace", body)
                self.assertIn("generation=" + generation, body)
            finally:
                self.cancel()
                self.assertEqual(running.result(timeout=8)[0], 409)

    def test_retrying_a_waiting_graph_after_cancellation_does_not_start_python(self):
        self.configure(limit=1)
        generation = self.token()
        (self.root / "behavior.json").write_text('{"delay": 10}')
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(self.load_graph, generation=generation)
            try:
                self.wait_for(lambda: bool(self.events()))
                self.assertEqual(self.request(2, generation=generation)[0], 202)
            finally:
                self.cancel()
            self.assertEqual(self.request(2, generation=generation)[0], 409)
            self.assertEqual(running.result(timeout=8)[0], 409)
        self.assertEqual(len([e for e in self.events() if e["event"] == "started"]), 1)

    def test_missing_semaphore_support_fails_without_starting_python(self):
        status, body = self.request(disabled="sem_get")
        self.assertEqual(status, 503)
        self.assertIn("sysvsem", body)
        self.assertEqual(self.events(), [])

    def test_read_only_prepared_csv_bypasses_queue_and_semaphore_dependency(self):
        self.configure(read_only=True)
        directory = self.root / "csv-files/panel"
        directory.mkdir()
        csv = "time,value\n2026-01-01,123\n"
        (directory / "panel-1.csv").write_text(csv)
        self.assertEqual(self.request(disabled="sem_get"), (200, csv))
        self.assertEqual(self.events(), [])

    def test_cross_site_requests_are_rejected_before_acquiring_a_slot(self):
        self.assertEqual(self.request(site="cross-site", disabled="sem_get")[0], 403)
        self.assertEqual(self.events(), [])

    def test_invalid_limits_fail_closed(self):
        for value in (0, -1, 33, "2.5", "true", '"two"'):
            with self.subTest(value=value):
                self.configure(limit=value)
                status, body = self.request()
                self.assertEqual(status, 503)
                self.assertIn("max_graph_generators", body)
                self.assertEqual(self.events(), [])

    def test_limit_can_change_after_the_queue_drains(self):
        self.configure(limit=1)
        self.assertEqual(self.request()[0], 200)
        (self.root / "events.jsonl").unlink()
        self.configure(limit=3)
        self.assertEqual([status for status, _ in self.parallel_requests()], [200] * 6)
        self.assertEqual(self.peak_generators(), 3)

    def test_changing_limit_while_busy_does_not_create_a_second_pool(self):
        self.configure(limit=1)
        (self.root / "behavior.json").write_text('{"delay": 0.4}')
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.load_graph, 1)
            deadline = time.monotonic() + 5
            while not self.events():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            self.configure(limit=2)
            second = pool.submit(self.load_graph, 2)
            time.sleep(0.1)
            self.assertEqual(len(self.events()), 1, "The existing pool must still limit generation")
            self.assertEqual(first.result(timeout=10)[0], 200)
            self.assertEqual(second.result(timeout=10)[0], 200)
        self.assertEqual(self.peak_generators(), 1)
        (self.root / "events.jsonl").unlink()
        self.assertEqual([status for status, _ in self.parallel_requests(4)], [200] * 4)
        self.assertEqual(self.peak_generators(), 2)

    def test_unexpected_php_worker_exit_releases_its_acquisition(self):
        self.configure(limit=1)
        holder = subprocess.Popen(
            [PHP, "-r", "require $argv[1]; $slot = acquire_graph_generator_slot(); "
             "echo 'held'; fflush(STDOUT); sleep(30);", str(self.root / "viewer-config.php")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            self.assertEqual(holder.stdout.read(4), b"held")
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.load_graph)
                time.sleep(0.2)
                self.assertEqual(self.events(), [], "Waiting requests must not start Python")
                holder.kill()
                holder.wait(timeout=5)
                self.assertEqual(future.result(timeout=10)[0], 200)
        finally:
            if holder.poll() is None:
                holder.kill()
            holder.communicate(timeout=5)

    def test_cancellation_stops_running_sql_and_discards_waiters_then_allows_fresh_requests(self):
        self.configure(limit=2)
        generation = self.token()
        (self.root / "behavior.json").write_text('{"delay": 10}')
        with ThreadPoolExecutor(max_workers=6) as pool:
            requests = [pool.submit(self.load_graph, graph, generation=generation) for graph in range(1, 7)]
            self.wait_for(lambda: len([e for e in self.events() if e["event"] == "started"]) == 2)
            status, body = self.cancel()
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["ok"])
            responses = [request.result(timeout=8) for request in requests]
        self.assertTrue(all(status == 409 and "Graph generation cancelled" in body for status, body in responses))
        self.assertEqual(len([e for e in self.events() if e["event"] == "started"]), 2)
        self.assertEqual(len([e for e in self.events() if e["event"] == "sql_cancelled"]), 2)
        self.assertEqual(self.peak_generators(), 2)
        self.assertNotEqual(self.token(), generation)
        (self.root / "behavior.json").write_text('{"delay": 0.1}')
        self.assertEqual(self.request(generation=self.token())[0], 200)

    def test_old_page_requests_are_cancelled_even_if_they_only_reach_php_afterwards(self):
        generation = self.token()
        self.assertEqual(self.cancel()[0], 200)
        status, body = self.request(generation=generation, disabled="sem_get")
        self.assertEqual(status, 409)
        self.assertIn("Reload the panel", body)
        self.assertEqual(self.events(), [])
        self.assertEqual(self.request(generation=generation, debug=True)[0], 409)

    def test_cancellation_requires_same_origin_post_and_editable_mode(self):
        generation = self.token()
        for origin in (None, "null", "https://attacker.invalid", "http://viewer.fixture.invalid"):
            self.assertEqual(self.cancel(origin=origin)[0], 403)
            self.assertEqual(self.token(), generation)
        self.assertEqual(self.request(endpoint="cancel-graph-generation.php")[0], 405)
        self.configure(read_only=True)
        self.assertEqual(self.cancel()[0], 403)
        self.assertEqual(self.token(), generation)

    def test_cancellation_does_not_signal_other_python_processes(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            generation = self.token()
            (self.root / "behavior.json").write_text('{"delay": 10}')
            with ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(self.load_graph, generation=generation, debug=True)
                self.wait_for(lambda: bool(self.events()))
                self.assertEqual(self.cancel()[0], 200)
                self.assertEqual(running.result(timeout=8)[0], 409)
            self.assertIsNone(other.poll())
        finally:
            other.terminate()
            other.wait(timeout=5)

    def test_unresponsive_child_is_killed_before_its_slot_is_reused(self):
        self.configure(limit=1)
        generation = self.token()
        (self.root / "behavior.json").write_text('{"delay": 10, "ignore_cancellation": true}')
        with ThreadPoolExecutor(max_workers=2) as pool:
            running = pool.submit(self.load_graph, generation=generation)
            self.wait_for(lambda: bool(self.events()))
            pid = self.events()[0]["pid"]
            self.cancel()
            self.assertEqual(running.result(timeout=8)[0], 409)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        (self.root / "behavior.json").write_text('{"delay": 0.1}')
        self.assertEqual(self.request(generation=self.token())[0], 200)

    def test_invalid_generation_tokens_never_start_python(self):
        for token in ("", "../outside", "0" * 31, [], "Z" * 32):
            self.assertEqual(self.request(generation=token)[0], 400)
        self.assertEqual(self.events(), [])

    def test_cancelling_during_output_keeps_the_previous_complete_csv(self):
        generation = self.token()
        directory = self.root / "csv-files/panel"
        directory.mkdir()
        destination = directory / "panel-1.csv"
        destination.write_text("previous complete CSV\n")
        (self.root / "behavior.json").write_text('{"delay": 0.05, "write_delay": 10}')
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(self.load_graph, generation=generation)
            self.wait_for(lambda: any(e["event"] == "writing" for e in self.events()))
            self.cancel()
            self.assertEqual(running.result(timeout=8)[0], 409)
        self.assertEqual(destination.read_text(), "previous complete CSV\n")
        self.assertEqual(list(directory.iterdir()), [destination])

    def test_python_cancels_its_query_when_the_owning_php_worker_dies(self):
        (self.root / "behavior.json").write_text('{"delay": 10}')
        process = subprocess.Popen(
            [PHP, "-r", HARNESS, str(self.root / "call-pyscript.php")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.environment, text=True,
        )
        try:
            process.stdin.write(json.dumps({
                "server": {"REQUEST_METHOD": "GET", "HTTP_HOST": "viewer.fixture.invalid",
                           "HTTP_SEC_FETCH_SITE": "same-origin"},
                "get": {"filename": "panel", "graphno": "1"},
            }))
            process.stdin.close()
            process.stdin = None
            self.wait_for(lambda: bool(self.events()))
            process.kill()
            process.wait(timeout=5)
            self.wait_for(lambda: any(e["event"] == "sql_cancelled" for e in self.events()))
            self.wait_for(lambda: any(e["event"] == "finished" for e in self.events()))
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()

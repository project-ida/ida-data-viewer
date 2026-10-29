"""Exercise output safety and connection ownership without a database or credentials."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("graph_generation", Path(__file__).resolve().parents[1] / "graph_generation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
GenerationControl = module.GenerationControl


class GraphGenerationControlTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ida-graph-output-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.destination = self.directory / "graph.csv"
        self.destination.write_text("old complete CSV\n")
        self.destination.chmod(0o640)
        self.control = GenerationControl()

    def test_complete_output_replaces_old_file_and_preserves_its_permissions(self):
        with self.control.output(self.destination) as temporary:
            temporary.write_text("new complete CSV\n")
            self.assertEqual(self.destination.read_text(), "old complete CSV\n")
        self.assertEqual(self.destination.read_text(), "new complete CSV\n")
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o640)
        self.assertEqual(list(self.directory.iterdir()), [self.destination])

    def test_failure_or_cancellation_preserves_previous_file_and_removes_temporary_output(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                control = GenerationControl()
                with self.assertRaises(SystemExit if cancel else OSError):
                    with control.output(self.destination) as temporary:
                        temporary.write_text("partly written")
                        if cancel:
                            control.cancelled.set()
                        else:
                            raise OSError("synthetic write failure")
                self.assertEqual(self.destination.read_text(), "old complete CSV\n")
                self.assertEqual(list(self.directory.iterdir()), [self.destination])

    def test_connection_is_closed_on_success_and_failure(self):
        class Connection:
            closed = False
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def close(self): self.closed = True
        for fail in (False, True):
            connection = Connection()
            try:
                with self.control.connection(lambda: connection) as current:
                    self.assertIs(current, connection)
                    if fail: raise ValueError("synthetic query failure")
            except ValueError:
                pass
            self.assertTrue(connection.closed)
            self.assertIsNone(self.control._connection)

    def test_cancelled_job_cannot_open_another_connection(self):
        self.control.cancelled.set()
        with self.assertRaises(SystemExit):
            with self.control.connection(lambda: self.fail("Cancelled job opened a connection")):
                self.fail("Cancelled job ran another query")

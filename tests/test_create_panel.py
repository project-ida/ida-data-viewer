"""Exercise panel creation with synthetic settings and isolated temporary files."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
PHP = shutil.which("php")
HARNESS = r"""
$request = json_decode(stream_get_contents(STDIN), true);
$_SERVER = $request['server'];
$_POST = $request['post'];
umask(0002);
register_shutdown_function(function () {
    fwrite(STDERR, "\nPANEL_TEST_STATUS=" . (http_response_code() ?: 200) . "\n");
});
require $argv[1];
"""


@unittest.skipUnless(PHP, "PHP CLI is required")
class CreatePanelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ida-create-panel-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.panels = self.root / "json-files"
        self.panels.mkdir()
        for name in ("create-panel.php", "viewer-config.php", "data-paths.php", "write-to-json.php"):
            shutil.copyfile(REPO / name, self.root / name)
        self.configure(read_only=False)

    def configure(self, read_only):
        (self.root / "config.ini").write_text(
            '[viewer]\nread_only = ' + ("true" if read_only else "false")
            + '\ndisplay_timezone = "UTC"\n'
        )

    def request(self, post=None, server=None, endpoint="create-panel.php"):
        headers = {
            "REQUEST_METHOD": "POST", "HTTP_HOST": "viewer.fixture.invalid",
            "HTTPS": "on", "HTTP_ORIGIN": "https://viewer.fixture.invalid",
        }
        headers.update(server or {})
        result = subprocess.run(
            [PHP, "-d", "display_errors=0", "-r", HARNESS, str(self.root / endpoint)],
            input=json.dumps({"server": headers, "post": post or {}}),
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        status = int(re.search(r"PANEL_TEST_STATUS=(\d+)", result.stderr)[1])
        return status, json.loads(result.stdout) if result.stdout else None

    def test_creates_valid_empty_panel_with_unicode_title_and_editable_permissions(self):
        status, response = self.request({"name": "Chamber α pressure"})
        self.assertEqual(status, 201)
        self.assertEqual(response, {"ok": True, "filename": "Chamber α pressure"})
        destination = self.panels / response["filename"]
        panel = json.loads(destination.read_text())
        self.assertEqual(set(panel), {"header", "start", "end", "notes", "graphs"})
        self.assertEqual(panel["header"], "Chamber α pressure")
        self.assertEqual(panel["graphs"], [])
        self.assertEqual(panel["notes"], "")
        self.assertEqual(panel["end"], "")
        start = datetime.fromisoformat(panel["start"])
        self.assertEqual((start.hour, start.minute, start.second), (0, 0, 0))
        self.assertEqual(destination.stat().st_mode & 0o777, 0o664)
        self.assertEqual(list(self.panels.iterdir()), [destination])
        self.assertFalse((self.root / "psql_credentials.py").exists())
        self.assertFalse((self.root / "csv-files").exists())

    def test_optional_json_extension_and_surrounding_spaces_are_normalised(self):
        status, response = self.request({"name": "  Detector-1.JSON  "})
        self.assertEqual(status, 201)
        self.assertEqual(response["filename"], "Detector-1")
        self.assertEqual(json.loads((self.panels / "Detector-1").read_text())["header"], "Detector-1")

    def test_rejects_invalid_names_without_creating_files(self):
        for name in (None, [], {}, "", " ", "../outside", "/outside", "nested/panel",
                     "bad\\name", ".hidden", "panel.php", "panel.json.php", "line\nbreak",
                     "<script>alert(1)</script>", "x" * 121):
            with self.subTest(name=name):
                status, response = self.request({"name": name})
                self.assertEqual(status, 400)
                self.assertFalse(response["ok"])
                self.assertEqual(list(self.panels.iterdir()), [])

    def test_rejects_missing_name(self):
        status, _ = self.request()
        self.assertEqual(status, 400)
        self.assertEqual(list(self.panels.iterdir()), [])

    def test_cannot_overwrite_json_or_extensionless_panels(self):
        for filename in ("Existing.json", "Existing", "Existing.JsOn"):
            with self.subTest(filename=filename):
                existing = self.panels / filename
                existing.write_bytes(b"original panel bytes\n")
                status, _ = self.request({"name": "Existing"})
                self.assertEqual(status, 409)
                self.assertEqual(existing.read_bytes(), b"original panel bytes\n")
                self.assertEqual(list(self.panels.iterdir()), [existing])
                existing.unlink()

    def test_existing_links_cannot_redirect_creation_outside_the_panel_directory(self):
        outside = self.root / "outside.json"
        outside.write_text("original outside data")
        for filename in ("Linked", "Linked.json"):
            with self.subTest(filename=filename):
                link = self.panels / filename
                link.symlink_to(outside)
                status, _ = self.request({"name": "Linked"})
                self.assertIn(status, (400, 409))
                self.assertEqual(outside.read_text(), "original outside data")
                self.assertTrue(link.is_symlink())
                link.unlink()

    def test_only_same_origin_posts_can_create_panels(self):
        for override, expected in (
            ({"REQUEST_METHOD": "GET"}, 405),
            ({"REQUEST_METHOD": "HEAD"}, 405),
            ({"HTTP_ORIGIN": None}, 403),
            ({"HTTP_ORIGIN": "null"}, 403),
            ({"HTTP_ORIGIN": "https://attacker.invalid"}, 403),
            ({"HTTP_ORIGIN": "http://viewer.fixture.invalid"}, 403),
            ({"HTTP_ORIGIN": "https://viewer.fixture.invalid:444"}, 403),
        ):
            with self.subTest(override=override):
                status, _ = self.request({"name": "Blocked"}, server=override)
                self.assertEqual(status, expected)
                self.assertEqual(list(self.panels.iterdir()), [])

    def test_read_only_mode_blocks_creation(self):
        self.configure(read_only=True)
        status, _ = self.request({"name": "Blocked"})
        self.assertEqual(status, 403)
        self.assertEqual(list(self.panels.iterdir()), [])

    def test_missing_configuration_blocks_creation(self):
        (self.root / "config.ini").unlink()
        status, _ = self.request({"name": "Blocked"})
        self.assertEqual(status, 503)
        self.assertEqual(list(self.panels.iterdir()), [])

    def test_simultaneous_requests_publish_exactly_one_complete_panel(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: self.request({"name": "Simultaneous"}), range(8)))
        statuses = [status for status, _ in responses]
        self.assertEqual(statuses.count(201), 1)
        self.assertEqual(statuses.count(409), 7)
        destination = self.panels / "Simultaneous"
        self.assertEqual(json.loads(destination.read_text())["graphs"], [])
        self.assertEqual(list(self.panels.iterdir()), [destination])

    def test_write_failure_leaves_no_partial_panel_or_temporary_file(self):
        self.panels.chmod(0o500)
        try:
            status, _ = self.request({"name": "Cannot-write"})
            self.assertEqual(status, 500)
            self.assertEqual(list(self.panels.iterdir()), [])
        finally:
            self.panels.chmod(0o700)

    def test_created_panel_can_be_saved_by_the_existing_editor_endpoint(self):
        _, response = self.request({"name": "Editable"})
        destination = self.panels / response["filename"]
        panel = json.loads(destination.read_text())
        panel["notes"] = "Edited through the panel editor"
        status, _ = self.request(
            {"filename": response["filename"], "json": json.dumps(panel)}, endpoint="write-to-json.php"
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(destination.read_text()), panel)


if __name__ == "__main__":
    unittest.main()

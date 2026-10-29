"""CMS path boundaries exercised through isolated, authenticated PHP requests."""

import hashlib
from html import unescape
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit


REPO = Path(__file__).resolve().parents[1]
PHP = shutil.which("php")
HARNESS = r'''
$request = json_decode(stream_get_contents(STDIN), true);
$root = $argv[1];
$_SERVER = ['REQUEST_METHOD' => $request['post'] === null ? 'GET' : 'POST',
    'HTTP_HOST' => 'viewer.fixture.invalid', 'HTTPS' => 'on',
    'HTTP_ORIGIN' => 'https://viewer.fixture.invalid', 'HTTP_USER_AGENT' => 'CMSFixture',
    'SCRIPT_NAME' => $request['url_prefix'].'/onefilecms/onefilecms.php',
    'SCRIPT_FILENAME' => $root.'/onefilecms/onefilecms.php', 'DOCUMENT_ROOT' => $request['document_root'],
    'SERVER_NAME' => 'viewer.fixture.invalid', 'REMOTE_ADDR' => '127.0.0.1', 'REMOTE_PORT' => '12345'];
$_GET = $request['get']; $_POST = $request['post'] ?? []; $_FILES = $request['files']; $_COOKIE = [];
if ($request['authenticated']) {
    session_save_path($root.'/sessions'); session_name('OFCMS'); session_id('cmsfixture123456789'); session_start();
    $_SESSION = ['valid' => 1, 'user_agent' => 'CMSFixture', 'last_active_time' => time(),
        'nuonce' => 'fixture-nonce', 'admin_page' => $request['admin'],
        'admin_ipath' => trim($root.'/json-files', '/').'/', 'recent_pages' => $request['recent_pages']];
    session_write_close(); $_COOKIE['OFCMS'] = 'cmsfixture123456789';
}
// Simulate an uploaded temporary file; the CMS's destination handling is unchanged.
function move_uploaded_file($from, $to) { return rename($from, $to); }
register_shutdown_function(function () {
    fwrite(STDERR, "\nCMS_TEST_STATUS=".(http_response_code() ?: 200)."\n");
});
require $root.'/onefilecms/onefilecms.php';
'''


@unittest.skipUnless(PHP, "PHP CLI is required")
class CMSPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ida-cms-path-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "data"
        self.root.mkdir()
        self.cms = self.root / "onefilecms"
        self.cms.mkdir()
        self.panels = self.root / "json-files"
        self.nested = self.panels / "Completed runs" / "Archive α"
        self.nested.mkdir(parents=True)
        (self.root / "sessions").mkdir()
        shutil.copyfile(REPO / "onefilecms/onefilecms.php", self.cms / "onefilecms.php")
        shutil.copyfile(REPO / "viewer-config.php", self.root / "viewer-config.php")
        (self.root / "config.ini").write_text('[viewer]\nread_only=false\ndisplay_timezone="UTC"\n')
        self.salt = "synthetic-test-salt"
        password_hash = "fixture-current"
        for _ in range(10001):
            password_hash = hashlib.sha256((password_hash + self.salt).encode()).hexdigest()
        (self.cms / "credentials.php").write_text(
            '<?php\n$USERNAME = "fixture";\n$HASHWORD = "' + password_hash
            + '";\n$SALT = "' + self.salt + '";\n'
        )
        self.outside = self.root / "outside.txt"
        self.outside.write_text("OUTSIDE-FIXTURE-CONTENT")
        self.lookalike = self.root / "json-files-other"
        self.lookalike.mkdir()
        (self.lookalike / "outside.txt").write_text("LOOKALIKE-FIXTURE-CONTENT")
        self.log = self.cms / "onefilecms.php-LOGIN.log"
        self.log.write_text("SYNTHETIC-LOGIN-LOG")
        (self.nested / "reading.txt").write_text("NESTED-FIXTURE-CONTENT")

    def request(self, *, folder=None, filename=None, page="index", post=None,
                authenticated=True, admin=False, files=None, recent_pages=None, url_prefix="", document_root=None):
        get = {"i": str(folder or self.panels).lstrip("/"), "p": page}
        if filename is not None:
            get["f"] = filename
        if post is not None:
            post = {"nuonce": "fixture-nonce", **post}
        result = subprocess.run(
            [PHP, "-d", "display_errors=0", "-d", "disable_functions=move_uploaded_file",
             "-r", HARNESS, str(self.root)],
            input=json.dumps({"get": get, "post": post, "authenticated": authenticated,
                              "admin": admin, "files": files or {}, "url_prefix": url_prefix,
                              "document_root": str(document_root or self.root),
                              "recent_pages": recent_pages or ["index", "index"]}),
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        self.php_diagnostics = result.stderr
        status = re.search(r"CMS_TEST_STATUS=(\d+)", result.stderr)
        self.assertIsNotNone(status, result.stderr)
        return int(status[1]), result.stdout

    def assert_blocked(self, **request):
        status, body = self.request(**request)
        self.assertEqual(status, 403, body[:600])
        self.assertNotIn("OUTSIDE-FIXTURE-CONTENT", body)
        self.assertEqual(self.outside.read_text(), "OUTSIDE-FIXTURE-CONTENT")

    def test_homepage_and_cms_links_follow_the_url_path_without_moving_panel_files(self):
        for prefix in ("", "/data", "/tools/data"):
            with self.subTest(prefix=prefix):
                status, body = self.request(url_prefix=prefix)
                self.assertEqual(status, 200)
                self.assertIn('<a id=website href="' + prefix + '/"', body)
                self.assertIn('href="' + prefix + '/onefilecms/onefilecms.php', body)
                status, body = self.request(folder=self.nested, filename="reading.txt",
                                            page="raw_view", url_prefix=prefix)
                self.assertEqual((status, body), (200, "NESTED-FIXTURE-CONTENT"))
                status, _ = self.request(folder=self.nested, url_prefix=prefix,
                                         post={"new_file": "Created under " + (prefix.replace('/', '-') or 'root')})
                self.assertEqual(status, 200)
                self.assertTrue((self.nested / ("Created under " + (prefix.replace('/', '-') or 'root'))).is_file())

    def test_favicon_url_matches_alias_and_existing_parent_document_root_layouts(self):
        (self.root / "favicon.ico").write_bytes(b"SYNTHETIC-APP-ICON")
        for prefix in ("", "/data", "/tools/data"):
            with self.subTest(alias_prefix=prefix):
                status, body = self.request(url_prefix=prefix)
                self.assertEqual(status, 200)
                self.assertIn('<img src="' + prefix + '/favicon.ico"', body)

        # Existing /htdocs/data installs use /htdocs/favicon.ico, even if the
        # viewer folder also contains an icon. Both paths are private fixtures.
        website_root = self.root.parent
        (website_root / "favicon.ico").write_bytes(b"SYNTHETIC-WEBSITE-ICON")
        status, body = self.request(url_prefix="/data", document_root=website_root)
        self.assertEqual(status, 200)
        self.assertIn('<a id=website href="/data/"', body)
        self.assertIn('<img src="/favicon.ico"', body)
        self.assertNotIn('<img src="/data/favicon.ico"', body)

    def test_authenticated_reads_and_edits_cannot_escape_with_a_filename(self):
        for admin in (False, True):
            for page in ("edit", "raw_view"):
                with self.subTest(admin=admin, page=page):
                    self.assert_blocked(filename="../outside.txt", page=page, admin=admin)
            self.assert_blocked(filename="../outside.txt", page="edit", admin=admin,
                                post={"filename": "signal", "contents": "overwritten"})

    def test_folder_prefixes_and_admin_mode_do_not_allow_outside_directory_browsing(self):
        for admin in (False, True):
            for folder in (self.root, self.lookalike):
                with self.subTest(admin=admin, folder=folder):
                    self.assert_blocked(folder=folder, admin=admin)

    def test_symlinks_cannot_redirect_reads_edits_uploads_or_creation_outside(self):
        (self.panels / "linked.txt").symlink_to(self.outside)
        (self.panels / "linked-folder").symlink_to(self.lookalike, target_is_directory=True)
        self.assert_blocked(filename="linked.txt", page="raw_view")
        self.assert_blocked(filename="linked.txt", page="edit", post={"filename": "signal", "contents": "overwritten"})
        self.assert_blocked(folder=self.panels / "linked-folder")
        self.assert_blocked(folder=self.panels / "linked-folder", post={"new_file": "created"})
        self.assertFalse((self.lookalike / "created").exists())

    def test_nested_reads_and_extensionless_or_json_file_edits_still_work(self):
        status, body = self.request(folder=self.nested, filename="reading.txt", page="raw_view")
        self.assertEqual(status, 200)
        self.assertEqual(body, "NESTED-FIXTURE-CONTENT")
        for filename in ("Detector α", "Detector β.json"):
            with self.subTest(filename=filename):
                target = self.nested / filename
                target.write_text('{"graphs":[]}')
                status, _ = self.request(folder=self.nested, filename=filename, page="edit",
                                         post={"filename": "signal", "contents": '{"notes":"changed"}'})
                self.assertEqual(status, 200)
                self.assertEqual(target.read_text(), '{"notes":"changed"}')

    def test_internal_file_and_folder_links_remain_usable(self):
        (self.panels / "linked-reading.txt").symlink_to(self.nested / "reading.txt")
        (self.panels / "linked-folder").symlink_to(self.nested, target_is_directory=True)
        for folder, filename in ((self.panels, "linked-reading.txt"), (self.panels / "linked-folder", "reading.txt")):
            status, body = self.request(folder=folder, filename=filename, page="raw_view")
            self.assertEqual((status, body), (200, "NESTED-FIXTURE-CONTENT"))

    def test_new_files_and_folders_stay_inside_the_nested_panel_directory(self):
        for field, name in (("new_file", "New panel"), ("new_file", "New panel.json"), ("new_folder", "New folder")):
            status, _ = self.request(folder=self.nested, post={field: name})
            self.assertEqual(status, 200)
            self.assertTrue((self.nested / name).exists())
        for field in ("new_file", "new_folder"):
            self.assert_blocked(post={field: "../forbidden"})
        self.assertFalse((self.root / "forbidden").exists())

    def test_copy_and_move_validate_both_source_and_destination(self):
        source = self.nested / "reading.txt"
        for field in ("copy_file", "rename_file"):
            for old, location in ((self.outside, ""), (source, "../json-files-other")):
                with self.subTest(field=field, old=old, location=location):
                    status, _ = self.request(folder=self.nested, post={field: "signal", "old_full_name": str(old).lstrip("/"),
                        "new_name": "forbidden.txt", "new_location": location})
                    self.assertIn(status, (200, 403))
                    self.assertEqual(self.outside.read_text(), "OUTSIDE-FIXTURE-CONTENT")
                    self.assertFalse((self.lookalike / "forbidden.txt").exists())
                    self.assertFalse((self.panels / "forbidden.txt").exists())
        location = "Completed runs/Archive α"
        status, _ = self.request(folder=self.nested, post={"copy_file": "signal", "old_full_name": str(source).lstrip("/"),
                                  "new_name": "Copy.json", "new_location": location})
        self.assertEqual(status, 200)
        self.assertEqual((self.nested / "Copy.json").read_text(), source.read_text())
        status, _ = self.request(folder=self.nested, post={"rename_file": "signal", "old_full_name": str(self.nested / "Copy.json").lstrip("/"),
                                  "new_name": "Renamed", "new_location": ""})
        self.assertEqual(status, 200)
        self.assertTrue((self.panels / "Renamed").is_file())
        self.assertFalse((self.nested / "Copy.json").exists())

    def test_move_and_copy_from_the_editor_display_the_destination_without_stale_file_warnings(self):
        for field in ("rename_file", "copy_file"):
            for suffix in ("", ".json", ".txt"):
                with self.subTest(action=field, suffix=suffix):
                    source = self.panels / (field + " source" + suffix)
                    source.write_text("POST-OPERATION-FIXTURE-CONTENT")
                    destination = self.nested / (field + " destination" + suffix)
                    page = "renamefile" if field == "rename_file" else "copyfile"
                    status, body = self.request(folder=self.panels, filename=source.name, page=page,
                        recent_pages=[page, "edit"], post={field: "signal",
                            "old_full_name": str(source).lstrip("/"), "new_name": destination.name,
                            "new_location": "Completed runs/Archive α"})
                    self.assertEqual(status, 200)
                    self.assertEqual(destination.read_text(), "POST-OPERATION-FIXTURE-CONTENT")
                    self.assertEqual(source.exists(), field == "copy_file")
                    self.assertNotIn("PHP Warning:", self.php_diagnostics)
                    self.assertNotIn("PHP Deprecated:", self.php_diagnostics)
                    self.assertNotIn("File is readonly", body)
                    form = re.search(r'<form id=edit_form[^>]+action="([^"]+)"', body)
                    self.assertIsNotNone(form)
                    query = parse_qs(urlsplit(unescape(form[1])).query)
                    self.assertEqual(query["f"], [destination.name])
                    self.assertEqual(query["p"], ["edit"])
                    if suffix == ".txt":
                        self.assertIn("POST-OPERATION-FIXTURE-CONTENT", body)

    def test_recursive_copy_checks_links_and_does_not_loop_through_ancestors(self):
        source = self.nested / "Folder"
        source.mkdir()
        (source / "reading.txt").write_text("recursive data")
        args = {"copy_file": "signal", "old_full_name": str(source).lstrip("/"), "new_name": "Copied", "new_location": ""}
        status, _ = self.request(folder=self.nested, post=args)
        self.assertEqual(status, 200)
        self.assertEqual((self.panels / "Copied/reading.txt").read_text(), "recursive data")
        (source / "outside.txt").symlink_to(self.outside)
        self.assert_blocked(folder=self.nested, post={**args, "new_name": "Blocked"})
        self.assertFalse((self.panels / "Blocked/outside.txt").exists())
        (source / "outside.txt").unlink()
        (source / "loop").symlink_to(source, target_is_directory=True)
        self.assert_blocked(folder=self.nested, post={**args, "new_name": "Loop"})

    def test_recursive_delete_unlinks_links_without_deleting_their_targets(self):
        target = self.nested / "Delete me"
        target.mkdir()
        (target / "outside-folder").symlink_to(self.lookalike, target_is_directory=True)
        (target / "outside-file").symlink_to(self.outside)
        (target / "nested").mkdir()
        (target / "nested/reading").write_text("delete this")
        status, _ = self.request(folder=self.nested, post={"mcd_del": "signal", "files": [target.name]})
        self.assertEqual(status, 200)
        self.assertFalse(target.exists())
        self.assertEqual(self.outside.read_text(), "OUTSIDE-FIXTURE-CONTENT")
        self.assertTrue((self.lookalike / "outside.txt").exists())
        self.assert_blocked(post={"mcd_del": "signal", "files": ["../outside.txt"]})

    def test_permission_changes_validate_the_full_filename(self):
        mode = self.outside.stat().st_mode
        self.assert_blocked(post={"new_perms": "600", "ipath": str(self.panels).lstrip("/") + "/", "perms_filename": "../outside.txt"})
        self.assertEqual(self.outside.stat().st_mode, mode)
        target = self.nested / "reading.txt"
        status, _ = self.request(post={"new_perms": "640", "ipath": str(self.nested).lstrip("/") + "/", "perms_filename": target.name})
        self.assertEqual(status, 200)
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_upload_destinations_are_checked_before_the_file_is_moved(self):
        temporary = self.root / "upload.tmp"
        temporary.write_text("uploaded panel")
        files = {"upload_file": {"name": ["../outside.txt"], "tmp_name": [str(temporary)], "error": [0], "size": [14]}}
        self.assert_blocked(page="uploaded", post={"ifexists": "overwrite"}, files=files)
        self.assertTrue(temporary.exists())
        files["upload_file"]["name"] = ["Uploaded panel.json"]
        status, _ = self.request(folder=self.nested, page="uploaded", post={"ifexists": "overwrite"}, files=files)
        self.assertEqual(status, 200)
        self.assertEqual((self.nested / "Uploaded panel.json").read_text(), "uploaded panel")

    def test_admin_source_preview_is_read_only_and_log_controls_still_work(self):
        source = self.cms / "onefilecms.php"
        original = source.read_bytes()
        status, body = self.request(folder=self.cms, filename=source.name, page="raw_view", admin=True)
        self.assertEqual(status, 200)
        self.assertIn("function CMS_Path_Allowed", body)
        self.assert_blocked(folder=self.cms, filename=source.name, page="edit", admin=True,
                            post={"filename": "signal", "contents": "overwritten"})
        self.assertEqual(source.read_bytes(), original)
        status, body = self.request(folder=self.cms, filename=self.log.name, page="raw_view", admin=True)
        self.assertEqual((status, body), (200, "SYNTHETIC-LOGIN-LOG"))
        status, _ = self.request(folder=self.cms, admin=True, post={"mcd_del": "signal", "files": [self.log.name]})
        self.assertEqual(status, 200)
        self.assertFalse(self.log.exists())
        self.assert_blocked(folder=self.cms, filename="credentials.php", page="raw_view", admin=True)

    def test_admin_source_copy_and_log_edit_remain_available(self):
        source = self.cms / "onefilecms.php"
        status, body = self.request(folder=self.cms, filename=source.name, page="copyfile", admin=True,
            recent_pages=["copyfile", "edit"], post={"copy_file": "signal", "old_full_name": str(source).lstrip("/"),
                  "new_name": "CMS-copy.txt", "new_location": ""})
        self.assertEqual(status, 200)
        self.assertEqual((self.panels / "CMS-copy.txt").read_bytes(), source.read_bytes())
        self.assertRegex(body, r'<textarea\s+id=file_editor')
        self.assertNotIn("PHP Warning:", self.php_diagnostics)
        status, _ = self.request(folder=self.cms, filename=self.log.name, page="edit", admin=True,
            post={"filename": "signal", "contents": "UPDATED-SYNTHETIC-LOG"})
        self.assertEqual(status, 200)
        self.assertEqual(self.log.read_text(), "UPDATED-SYNTHETIC-LOG")

    def test_admin_cannot_rename_delete_or_chmod_the_running_cms(self):
        source = self.cms / "onefilecms.php"
        original = source.read_bytes()
        mode = source.stat().st_mode
        operations = [
            {"rename_file": "signal", "old_full_name": str(source).lstrip("/"),
             "new_name": "Moved.php", "new_location": ""},
            {"mcd_del": "signal", "files": [source.name]},
            {"new_perms": "600", "ipath": str(self.cms).lstrip("/") + "/", "perms_filename": source.name},
        ]
        for post in operations:
            with self.subTest(action=next(iter(post))):
                self.assert_blocked(folder=self.cms, filename=source.name, page="edit", admin=True, post=post)
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(source.stat().st_mode, mode)

    def test_admin_links_and_return_navigation_use_the_exact_files_and_saved_panel_folder(self):
        status, body = self.request(page="admin")
        self.assertEqual(status, 200)
        self.assertIn("?i=" + str(self.cms).lstrip("/") + "/&amp;f=onefilecms.php", body)
        status, body = self.request(folder=self.cms, page="admin", admin=True)
        self.assertEqual(status, 200)
        self.assertIn("Administration Options", body)

    def test_admin_credential_updates_still_use_the_dedicated_private_file(self):
        status, _ = self.request(page="changeun", admin=True,
            post={"un": "signal", "password": "fixture-current", "new1": "changed_user", "new2": "changed_user"})
        self.assertEqual(status, 200)
        self.assertIn("changed_user", (self.cms / "credentials.php").read_text())

    def test_anonymous_and_read_only_requests_remain_blocked(self):
        status, body = self.request(filename="../outside.txt", page="edit", authenticated=False)
        self.assertEqual(status, 200)
        self.assertNotIn("OUTSIDE-FIXTURE-CONTENT", body)
        (self.root / "config.ini").write_text('[viewer]\nread_only=true\n')
        self.assert_blocked()


if __name__ == "__main__":
    unittest.main()

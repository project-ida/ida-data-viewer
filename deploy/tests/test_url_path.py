"""Check deployment URL mappings with invented files and a private Apache instance.

PHP entry points are inert markers here: these tests exercise Apache routing and
access control. The separate CMS tests exercise PHP with a mounted SCRIPT_NAME.
"""

import base64
import http.client
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import unittest

try:
    import jinja2
    import yaml
except ImportError:
    jinja2 = yaml = None


REPO = Path(__file__).resolve().parents[2]
APACHE = shutil.which("apache2") or shutil.which("httpd")
HTPASSWD = shutil.which("htpasswd")
OPENSSL = shutil.which("openssl")
MODULES = next((p for p in (Path("/usr/lib/apache2/modules"), Path("/usr/libexec/apache2"))
                if (p / "mod_ssl.so").exists()), None)


def template_environment():
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
    environment.filters["regex_escape"] = re.escape
    environment.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(pattern, replacement, value)
    environment.tests["match"] = lambda value, pattern: bool(re.match(pattern, value))
    return environment


@unittest.skipUnless(jinja2 and yaml, "Jinja2 and PyYAML are required")
class URLPathSettingsTests(unittest.TestCase):
    def test_default_trailing_slash_and_invalid_paths(self):
        defaults = yaml.safe_load((REPO / "deploy/ansible/defaults.yml").read_text())
        tasks = yaml.safe_load((REPO / "deploy/ansible/tasks/preflight.yml").read_text())
        validation = next(t for t in tasks if t["name"] == "Validate the viewer URL path")
        assertions = validation["ansible.builtin.assert"]["that"]
        environment = template_environment()

        def valid(path):
            return all(environment.compile_expression(expression)(ida_url_path=path) for expression in assertions)

        self.assertEqual(defaults["ida_url_path"], "/")
        for path, prefix in (("/", ""), ("/data", "/data"), ("/data/", "/data"),
                             ("/tools/data", "/tools/data"), ("/tools/data/", "/tools/data"),
                             ("/Viewer_2-graphs", "/Viewer_2-graphs")):
            with self.subTest(path=path):
                self.assertTrue(valid(path))
                self.assertEqual(environment.from_string(defaults["ida_url_prefix"]).render(ida_url_path=path), prefix)

        for path in (None, 1, [], "", "data", "//data", "/data//", "/../data", "/data/..",
                     "/data space", "/data\n", "/data?x=1", "/data#part", "/data%2fextra",
                     '/data"', "/data\\extra", "https://viewer.fixture.invalid/data"):
            with self.subTest(path=path):
                self.assertFalse(valid(path))


@unittest.skipUnless(jinja2 and yaml and APACHE and HTPASSWD and OPENSSL and MODULES
                     and hasattr(os, "geteuid") and os.geteuid() != 0,
                     "An unprivileged user, Apache modules, OpenSSL, htpasswd, Jinja2 and PyYAML are required")
class ApacheURLPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ida-apache-url-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        self.private = self.root / "private"
        self.private.mkdir()
        self.acme = self.root / "acme/.well-known/acme-challenge"
        self.acme.mkdir(parents=True)
        (self.acme / "fixture-token").write_text("ACME-FIXTURE")
        # No actual application secrets, measurement files, or database connections.
        self.public_paths = ("index.php", "load-panel.php", "create-panel.php", "style.css",
                             "onefilecms/onefilecms.php", "postgres/adminer-6.2.0-en.php",
                             "json-files/nested/panel", "csv-files/graph.csv")
        self.denied_paths = ("config.ini", "psql_credentials.py", "deploy/settings.yml", ".git/HEAD",
                             "onefilecms/onefilecms.php-LOGIN.log", "viewer-config.php",
                             "json-files/load-panel.php", "csv-files/index.php", "nested/index.php")
        for path in self.public_paths + self.denied_paths:
            target = self.checkout / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("ROUTING-FIXTURE:" + path)
        subprocess.run([HTPASSWD, "-iBc", str(self.private / "website.htpasswd"), "fixture"],
                       input="synthetic-test-password\n", capture_output=True, text=True, check=True)
        subprocess.run([OPENSSL, "req", "-x509", "-nodes", "-newkey", "rsa:2048", "-days", "1",
                        "-subj", "/CN=viewer.fixture.invalid", "-keyout", str(self.root / "privkey.pem"),
                        "-out", str(self.root / "fullchain.pem")], capture_output=True, check=True)

    def start_apache(self, url_path):
        self.prefix = url_path.rstrip("/")
        with socket.socket() as http_socket, socket.socket() as https_socket:
            http_socket.bind(("127.0.0.1", 0))
            https_socket.bind(("127.0.0.1", 0))
            self.http_port = http_socket.getsockname()[1]
            self.https_port = https_socket.getsockname()[1]
        environment = template_environment()
        template = (REPO / "deploy/ansible/templates/apache.conf.j2").read_text()
        rendered = environment.from_string(template).render(
            ida_hostname="viewer.fixture.invalid", ida_web_root=str(self.checkout),
            ida_url_prefix=self.prefix, ida_acme_root=str(self.root / "acme"), ida_https_ready=True,
            ida_certificate_dir=str(self.root), ida_private_dir=str(self.private),
            ida_cache_dir=str(self.root / "cache"),
        ).replace("<VirtualHost *:80>", f"<VirtualHost *:{self.http_port}>") \
         .replace("<VirtualHost *:443>", f"<VirtualHost *:{self.https_port}>")
        names = ("mpm_prefork", "unixd", "authn_core", "authn_file", "authz_core", "authz_user",
                 "auth_basic", "alias", "dir", "env", "headers", "log_config", "ssl")
        compiled = subprocess.run([APACHE, "-l"], capture_output=True, text=True, check=True).stdout
        loads = []
        for name in names:
            if f"mod_{name}.c" not in compiled and f"{name}.c" not in compiled:
                module = MODULES / f"mod_{name}.so"
                self.assertTrue(module.exists(), str(module))
                loads.append(f'LoadModule {name}_module "{module}"')
        config = self.root / "httpd.conf"
        config.write_text(f'''ServerRoot "{self.root}"
DefaultRuntimeDir "{self.root}"
PidFile "{self.root}/httpd.pid"
Listen 127.0.0.1:{self.http_port}
Listen 127.0.0.1:{self.https_port}
ServerName viewer.fixture.invalid
User #{os.getuid()}
Group #{os.getgid()}
Define APACHE_LOG_DIR "{self.root}"
ErrorLog "{self.root}/server-error.log"
LogLevel warn
''' + "\n".join(loads) + '\n<Directory />\nRequire all denied\n</Directory>\n' + rendered)
        check = subprocess.run([APACHE, "-t", "-f", str(config)], capture_output=True, text=True, timeout=10)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.output = (self.root / "process.log").open("w+")
        self.addCleanup(self.output.close)
        self.server = subprocess.Popen([APACHE, "-X", "-f", str(config)],
                                       stdout=self.output, stderr=self.output)
        self.addCleanup(self.stop_apache)
        deadline = time.monotonic() + 5
        while True:
            self.assertIsNone(self.server.poll(), self.logs())
            try:
                if self.request(self.prefix + "/index.php")[0] == 401:
                    break
            except (OSError, http.client.HTTPException):
                pass
            self.assertLess(time.monotonic(), deadline, self.logs())
            time.sleep(0.025)

    def logs(self):
        return "\n".join(p.read_text(errors="replace") for p in self.root.glob("*.log"))

    def stop_apache(self):
        if self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=5)

    def request(self, path, authenticated=False, secure=True):
        if secure:
            connection = http.client.HTTPSConnection("127.0.0.1", self.https_port, timeout=2,
                                                     context=ssl._create_unverified_context())
        else:
            connection = http.client.HTTPConnection("127.0.0.1", self.http_port, timeout=2)
        headers = {"Host": "viewer.fixture.invalid"}
        if authenticated:
            headers["Authorization"] = "Basic " + base64.b64encode(b"fixture:synthetic-test-password").decode()
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            connection.close()

    def check_mapping(self):
        self.assertEqual(self.request(self.prefix + "/")[0], 401)
        self.assertEqual(self.request(self.prefix + "/", authenticated=True)[2], "ROUTING-FIXTURE:index.php")
        for path in self.public_paths:
            with self.subTest(path=path):
                status, _, body = self.request(self.prefix + "/" + path, authenticated=True)
                self.assertEqual((status, body), (200, "ROUTING-FIXTURE:" + path), self.logs())
        for path in self.denied_paths:
            with self.subTest(path=path):
                self.assertEqual(self.request(self.prefix + "/" + path, authenticated=True)[0], 403, self.logs())
        self.assertEqual(self.request("/.well-known/acme-challenge/fixture-token", secure=False)[2], "ACME-FIXTURE")
        status, headers, _ = self.request(self.prefix + "/load-panel.php?filename=nested/panel", secure=False)
        self.assertEqual(status, 301)
        self.assertEqual(headers["Location"], "https://viewer.fixture.invalid" + self.prefix
                         + "/load-panel.php?filename=nested/panel")
        if self.prefix:
            status, headers, _ = self.request(self.prefix)
            self.assertEqual((status, headers["Location"]), (301, "https://viewer.fixture.invalid" + self.prefix + "/"))
            for path in ("/", "/index.php", "/style.css", "/onefilecms/onefilecms.php",
                         self.prefix + "-other/index.php"):
                for authenticated in (False, True):
                    with self.subTest(outside=path, authenticated=authenticated):
                        self.assertEqual(self.request(path, authenticated=authenticated)[0], 403, self.logs())

    def test_hostname_root(self):
        self.start_apache("/")
        self.check_mapping()

    def test_data_subpath(self):
        self.start_apache("/data")
        self.check_mapping()

    def test_nested_subpath_with_optional_trailing_slash(self):
        self.start_apache("/tools/data/")
        self.check_mapping()


if __name__ == "__main__":
    unittest.main()

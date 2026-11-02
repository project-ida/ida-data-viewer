"""Exercise TLS file installation and renewal in temporary directories.

Certificates are invented fixtures. No real settings, services, ACME requests
or database connections are used; the reload target is a recording stub.
"""

import grp
import os
from pathlib import Path
import pwd
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    import jinja2
    import yaml
except ImportError:
    jinja2 = yaml = None


REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "deploy/ansible/templates"
ANSIBLE = os.environ.get("IDA_TEST_ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")


class TLSFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ida-postgresql-tls-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.issued = self.root / "issued certificates"
        self.issued.mkdir()
        self.tls = self.root / "postgresql tls"
        self.tls.mkdir(mode=0o700)
        self.calls = self.root / "reloads.log"
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.group = grp.getgrgid(os.getgid()).gr_name
        self.variables = {
            "ida_certificate_dir": str(self.issued),
            "ida_postgresql_tls_dir": str(self.tls),
            "ida_postgresql_service": "postgresql@16-main",
            "ida_postgresql_version": "16",
        }
        self.issue("first")

    def issue(self, version):
        (self.issued / "fullchain.pem").write_text("Invented certificate " + version + "\n")
        (self.issued / "privkey.pem").write_text("Invented private key " + version + "\n")
        (self.issued / "privkey.pem").chmod(0o600)

    def assert_installed(self, version):
        self.assertEqual((self.tls / "server.crt").read_text(), "Invented certificate " + version + "\n")
        self.assertEqual((self.tls / "server.key").read_text(), "Invented private key " + version + "\n")
        self.assertEqual((self.tls / "server.crt").stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.tls / "server.key").stat().st_mode & 0o777, 0o640)

    def reloads(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []


@unittest.skipUnless(jinja2, "Jinja2 is required")
class PostgreSQLTLSRenewalTests(TLSFixture):
    def setUp(self):
        super().setUp()
        # Substitute only ownership and the service command for an unprivileged
        # fixture; all staging, copy, cleanup and lineage logic is the real hook.
        source = (TEMPLATES / "postgresql-renew.sh.j2").read_text()
        source = source.replace("-o root -g postgres", f"-o {self.user} -g {self.group}")
        systemctl = self.root / "record-reload"
        systemctl.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + shlex.quote(str(self.calls)) + "\n")
        systemctl.chmod(0o700)
        source = source.replace("/usr/bin/systemctl", shlex.quote(str(systemctl)))
        environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
        environment.filters["quote"] = shlex.quote
        self.hook = self.root / "renew-hook"
        self.hook.write_text(environment.from_string(source).render(**self.variables))
        for name, source_name in (("server.crt", "fullchain.pem"), ("server.key", "privkey.pem")):
            shutil.copyfile(self.issued / source_name, self.tls / name)
        (self.tls / "server.key").chmod(0o640)

    def renew(self, lineage, succeeds=True):
        environment = {"PATH": os.environ["PATH"]}
        if lineage is not None:
            environment["RENEWED_LINEAGE"] = str(lineage)
        result = subprocess.run(["sh", str(self.hook)], env=environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode == 0, succeeds, result.stdout + result.stderr)
        self.assertEqual(list(self.tls.glob(".renew.*")), [])

    def test_matching_renewal_replaces_both_files_with_private_key_permissions(self):
        self.issue("renewed")
        self.renew(self.issued)
        self.assert_installed("renewed")
        self.assertEqual(self.reloads(), ["reload postgresql@16-main"])

    def test_another_certificate_does_not_replace_files_or_reload(self):
        self.issue("unrelated")
        self.renew(self.root / "another certificate")
        self.assert_installed("first")
        self.assertEqual(self.reloads(), [])

    def test_no_lineage_does_not_replace_files_or_reload(self):
        self.issue("unrelated")
        self.renew(None)
        self.assert_installed("first")
        self.assertEqual(self.reloads(), [])

    def test_missing_renewed_key_preserves_the_previous_pair_and_does_not_reload(self):
        self.issue("incomplete")
        (self.issued / "privkey.pem").unlink()
        self.renew(self.issued, succeeds=False)
        self.assert_installed("first")
        self.assertEqual(self.reloads(), [])


@unittest.skipUnless(ANSIBLE and yaml, "Ansible and PyYAML are required")
class PostgreSQLTLSInstallTests(TLSFixture):
    def setUp(self):
        super().setUp()
        self.configuration = self.root / "conf.d"
        self.configuration.mkdir()
        self.hooks = self.root / "renewal-hooks"
        self.hooks.mkdir()
        tasks = yaml.safe_load((REPO / "deploy/ansible/tasks/postgresql-tls.yml").read_text())
        for task in tasks:
            for module in ("ansible.builtin.file", "ansible.builtin.copy", "ansible.builtin.template"):
                options = task.get(module)
                if options is None:
                    continue
                # Keep the real tasks and modules, redirecting privileged paths
                # and ownership to this temporary unprivileged installation.
                options["owner"] = self.user
                options["group"] = self.group
                if "dest" in options and options["dest"].startswith("/etc/postgresql/"):
                    options["dest"] = str(self.configuration / "ida-tls.conf")
                elif "dest" in options and options["dest"].startswith("/etc/letsencrypt/"):
                    options["dest"] = str(self.hooks / "ida-postgresql-reload")
                if module == "ansible.builtin.template":
                    options["src"] = str(TEMPLATES / options["src"])
        self.playbook = self.root / "fixture.yml"
        self.playbook.write_text(yaml.safe_dump([{
            "name": "TLS installation fixture", "hosts": "all", "gather_facts": False,
            "vars": {**self.variables, "ansible_python_interpreter": sys.executable},
            "tasks": tasks,
            "handlers": [{
                "name": "Reload PostgreSQL", "ansible.builtin.command": {
                    "argv": [sys.executable, "-c",
                             "from pathlib import Path; import sys; "
                             "f=Path(sys.argv[1]).open('a'); f.write('reload\\n'); f.close()",
                             str(self.calls)],
                }, "changed_when": True,
            }],
        }]))
        self.environment = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR") if key in os.environ}
        self.environment.update(
            ANSIBLE_CONFIG=str(REPO / "deploy/ansible.cfg"),
            ANSIBLE_HOME=str(self.root / "ansible-home"),
            ANSIBLE_LOCAL_TEMP=str(self.root / "local-temp"),
            ANSIBLE_REMOTE_TEMP=str(self.root / "remote-temp"), ANSIBLE_NOCOLOR="1",
        )

    def install(self):
        result = subprocess.run([ANSIBLE, "-i", "localhost,", "-c", "local", str(self.playbook)],
                                env=self.environment, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_install_sets_permissions_and_tls_without_changing_the_listener(self):
        self.install()
        self.assert_installed("first")
        self.assertEqual(self.tls.stat().st_mode & 0o777, 0o750)
        self.assertEqual((self.hooks / "ida-postgresql-reload").stat().st_mode & 0o777, 0o755)
        config = (self.configuration / "ida-tls.conf").read_text()
        self.assertIn("ssl = on", config)
        self.assertIn(str(self.tls / "server.crt"), config)
        self.assertNotIn("listen_addresses", config)
        self.assertEqual(self.reloads(), ["reload"])

    def test_rerun_is_idempotent_and_updated_certificate_triggers_one_reload(self):
        self.install()
        self.install()
        self.assertEqual(self.reloads(), ["reload"])
        self.issue("updated")
        self.install()
        self.assert_installed("updated")
        self.assertEqual(self.reloads(), ["reload", "reload"])


if __name__ == "__main__":
    unittest.main()

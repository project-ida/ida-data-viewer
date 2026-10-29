"""Run only the example Ansible tasks against temporary fixtures and a fake seed.

No packages, services, real settings or database connections are used.
"""

import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    import yaml
except ImportError:
    yaml = None


REPO = Path(__file__).resolve().parents[2]
ANSIBLE = os.environ.get("IDA_TEST_ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")
FAKE_SEED = '''import argparse
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--dataset', required=True, choices=['all'])
parser.add_argument('--settings', required=True, type=Path)
parser.add_argument('--if-missing', action='store_true', required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
assert args.settings.read_text() == 'Invented fixture settings.\\n'
with (root / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps({'cwd': str(Path.cwd()), 'settings': str(args.settings)}) + '\\n')
for name in ('pressure', 'pulses'):
    table = root / (name + '.fixture')
    if not table.exists():
        table.write_text('SYNTHETIC-DATA')
if (root / 'fail-once').exists():
    (root / 'fail-once').unlink()
    sys.exit('Synthetic interruption after database commit')
panel = root / 'json-files/example-panel'
if not panel.exists():
    panel.write_text('{"header":"Synthetic fixture panel","graphs":[]}')
'''


@unittest.skipUnless(ANSIBLE and yaml, "Ansible and PyYAML are required")
class InstallExampleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ida-install-example-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.checkout = self.root / "checkout"
        (self.checkout / "deploy").mkdir(parents=True)
        (self.checkout / "deploy/seed-demo.py").write_text(FAKE_SEED)
        (self.checkout / "json-files").mkdir()
        self.state = self.root / "private-state"
        self.state.mkdir(mode=0o700)
        self.marker = self.state / "example-data-created"
        self.panel = self.checkout / "json-files/example-panel"
        self.settings = self.root / "invented settings.yml"
        self.settings.write_text("Invented fixture settings.\n")
        self.settings.chmod(0o600)
        self.playbook = self.root / "fixture.yml"
        self.playbook.write_text(yaml.safe_dump([{
            "name": "Example setup with isolated fixtures", "hosts": "all", "gather_facts": False,
            "vars": {
                "ansible_python_interpreter": sys.executable,
                "ida_source_dir": str(self.checkout), "ida_web_root": str(self.checkout),
                "ida_state_dir": str(self.state), "ida_settings_file": str(self.settings),
                "ida_install_user": pwd.getpwuid(os.getuid()).pw_name,
                "ida_install_group": grp.getgrgid(os.getgid()).gr_name,
            },
            "tasks": [{"ansible.builtin.import_tasks": str(REPO / "deploy/ansible/tasks/example.yml")}],
        }]))
        self.environment = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR") if key in os.environ}
        self.environment.update(
            ANSIBLE_CONFIG=str(REPO / "deploy/ansible.cfg"), ANSIBLE_HOME=str(self.root / "ansible-home"),
            ANSIBLE_LOCAL_TEMP=str(self.root / "local-temp"), ANSIBLE_REMOTE_TEMP=str(self.root / "remote-temp"),
            ANSIBLE_NOCOLOR="1",
        )

    def install(self, succeeds=True):
        result = subprocess.run([ANSIBLE, "-i", "localhost,", "-c", "local", str(self.playbook)],
                                env=self.environment, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode == 0, succeeds, result.stdout + result.stderr)

    def calls(self):
        return [json.loads(line) for line in (self.checkout / "calls.jsonl").read_text().splitlines()]

    def test_first_install_seeds_from_checkout_and_records_success(self):
        self.install()
        self.assertEqual(self.calls(), [{"cwd": str(self.checkout), "settings": str(self.settings)}])
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.marker.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.panel.stat().st_uid, os.getuid())
        self.assertEqual(self.panel.stat().st_mode & 0o777, 0o664)

    def test_rerun_does_not_reseed_or_recreate_deliberately_deleted_examples(self):
        self.install()
        self.panel.unlink()
        (self.checkout / "pressure.fixture").unlink()
        self.install()
        self.assertEqual(len(self.calls()), 1)
        self.assertFalse(self.panel.exists())
        self.assertFalse((self.checkout / "pressure.fixture").exists())

    def test_existing_data_and_edited_panel_are_preserved(self):
        (self.checkout / "pressure.fixture").write_text("EXISTING-DATA")
        self.panel.write_text('{"header":"User edited this panel"}')
        self.panel.chmod(0o640)
        self.install()
        self.assertEqual((self.checkout / "pressure.fixture").read_text(), "EXISTING-DATA")
        self.assertEqual(self.panel.read_text(), '{"header":"User edited this panel"}')
        self.assertEqual(self.panel.stat().st_mode & 0o777, 0o640)
        self.assertTrue(self.marker.exists())

    def test_interrupted_setup_is_retried_before_marking_success(self):
        (self.checkout / "fail-once").touch()
        self.install(succeeds=False)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.panel.exists())
        (self.checkout / "pressure.fixture").write_text("KEEP-COMMITTED-DATA")
        self.install()
        self.assertTrue(self.marker.exists())
        self.assertTrue(self.panel.exists())
        self.assertEqual((self.checkout / "pressure.fixture").read_text(), "KEEP-COMMITTED-DATA")
        self.assertEqual(len(self.calls()), 2)


if __name__ == "__main__":
    unittest.main()

"""Check acquisition validation and real grants using an isolated PostgreSQL cluster.

Set IDA_TEST_PG_BINDIR and IDA_TEST_ANSIBLE_PLAYBOOK if the binaries are not on
PATH. All database files, accounts, passwords and certificates are fixtures.
No real deployment settings or existing database connections are used.
"""

import base64
import copy
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

try:
    import jinja2
    import yaml
except ImportError:
    jinja2 = yaml = None
try:
    import psycopg2
except ImportError:
    psycopg2 = None


REPO = Path(__file__).resolve().parents[2]
ANSIBLE = os.environ.get("IDA_TEST_ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")
POSTGRES = shutil.which("postgres")
PG_BIN = Path(os.environ["IDA_TEST_PG_BINDIR"]) if os.environ.get("IDA_TEST_PG_BINDIR") else (
    Path(POSTGRES).parent if POSTGRES else None)
OPENSSL = shutil.which("openssl")
SETTINGS = {
    "ida_hostname": "viewer.fixture.invalid", "ida_letsencrypt_email": "fixture@example.invalid",
    "ida_database_name": "fixture_measurements", "ida_database_port": 5432,
    "ida_database_admin": "fixture_admin", "ida_database_admin_password": "invented-admin-1234",
    "ida_database_reader": "fixture_reader", "ida_database_reader_password": "invented-reader-1234",
    "ida_database_acquisition": "fixture_acquisition", "ida_database_acquisition_password": "invented-acquisition-1234",
    "ida_web_username": "fixture_web", "ida_web_password": "invented-web-1234",
    "ida_cms_username": "fixture_cms", "ida_cms_password": "invented-cms-1234",
}


@unittest.skipUnless(yaml and jinja2, "PyYAML and Jinja2 are required")
class AcquisitionSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tasks = yaml.safe_load((REPO / "deploy/ansible/tasks/preflight.yml").read_text())
        self.environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
        self.environment.tests["match"] = lambda value, pattern: isinstance(value, str) and bool(re.match(pattern, value))
        self.environment.filters["b64decode"] = lambda value: base64.b64decode(value).decode()
        self.environment.filters["from_json"] = json.loads

    def valid(self, settings):
        task = next(t for t in self.tasks if t["name"] == "Validate host, database and account names")
        return all(self.environment.compile_expression(expression)(**settings)
                   for expression in task["ansible.builtin.assert"]["that"])

    def test_acquisition_requires_a_distinct_unprivileged_account_name(self):
        self.assertTrue(self.valid(SETTINGS))
        for name in ("fixture_admin", "fixture_reader", "postgres", "public", "pg_acquisition", "bad-name", ""):
            with self.subTest(name=name):
                self.assertFalse(self.valid({**SETTINGS, "ida_database_acquisition": name}))
        for key in ("ida_database_acquisition", "ida_database_acquisition_password"):
            settings = dict(SETTINGS)
            del settings[key]
            self.assertFalse(self.valid(settings))

    def test_acquisition_password_uses_the_existing_password_policy(self):
        task = next(t for t in self.tasks if t["name"].startswith("Validate 12-72 character ASCII passwords"))
        expressions = task["ansible.builtin.assert"]["that"]
        for value, expected in (("invented-acquisition-1234", True), ("short", False),
                                ("contains spaces here", False), ("x" * 73, False), ("CHANGE_ME", False)):
            with self.subTest(expected=expected):
                settings = {**SETTINGS, "ida_database_acquisition_password": value}
                items = [self.environment.from_string(item).render(**settings) for item in task["loop"]]
                actual = all(self.environment.compile_expression(e)(item=item)
                             for item in items for e in expressions)
                self.assertEqual(actual, expected)

    def test_installation_identity_keeps_accounts_and_database_fixed(self):
        task = next(t for t in self.tasks if t["name"].startswith("Preserve the database and installation identity"))
        expression = self.environment.compile_expression(task["ansible.builtin.assert"]["that"][0])
        identity = {"database": "fixture_measurements", "admin": "fixture_admin",
                    "reader": "fixture_reader", "acquisition": "fixture_acquisition"}

        def accepted(previous):
            content = base64.b64encode(json.dumps(previous).encode()).decode()
            return expression(ida_installation_identity=identity,
                              ida_previous_installation={"content": content})

        self.assertTrue(accepted(identity))
        self.assertFalse(accepted({**identity, "acquisition": "another_acquisition"}))
        self.assertFalse(accepted({**identity, "database": "another_database"}))


@unittest.skipUnless(yaml and jinja2 and psycopg2 and ANSIBLE and PG_BIN and OPENSSL
                     and hasattr(os, "geteuid") and os.geteuid() != 0,
                     "Unprivileged PostgreSQL binaries, Ansible, OpenSSL and psycopg2 are required")
class DatabaseAcquisitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ida-db-acquisition-", dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.socket = self.root / "sock"
        self.socket.mkdir()
        self.passfile = str(self.root / "unused-password-file")
        self.data = self.root / "data"
        self.settings = dict(SETTINGS)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.settings["ida_database_port"] = listener.getsockname()[1]
        self.run_command([str(PG_BIN / "initdb"), "-D", str(self.data), "-U", "postgres",
                          "--auth-local=trust", "--auth-host=reject", "--no-locale", "-E", "UTF8"])
        self.run_command([OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                          "-subj", "/CN=viewer.fixture.invalid", "-keyout", str(self.root / "server.key"),
                          "-out", str(self.root / "server.crt")])
        (self.root / "server.key").chmod(0o600)
        source = (REPO / "deploy/ansible/templates/pg_hba.conf.j2").read_text()
        # The fixture's private socket uses trust instead of OS postgres peer
        # authentication; TCP uses the real installed password and TLS rules.
        source = "local all all trust\n" + "\n".join(
            line for line in source.splitlines() if not line.startswith("local"))
        (self.data / "pg_hba.conf").write_text(jinja2.Environment().from_string(source).render(**self.settings))
        with (self.data / "postgresql.conf").open("a") as stream:
            stream.write(f"\nlisten_addresses = '127.0.0.1'\nport = {self.settings['ida_database_port']}\n"
                         f"unix_socket_directories = '{self.socket}'\nssl = on\n"
                         f"ssl_cert_file = '{self.root / 'server.crt'}'\n"
                         f"ssl_key_file = '{self.root / 'server.key'}'\n")
        self.addCleanup(self.stop_database)
        self.run_command([str(PG_BIN / "pg_ctl"), "-D", str(self.data), "-l", str(self.root / "postgres.log"), "-w", "start"])
        self.sql("postgres", "CREATE ROLE fixture_admin LOGIN NOSUPERUSER", database="postgres")
        self.sql("postgres", "CREATE DATABASE fixture_measurements OWNER fixture_admin", database="postgres")
        self.sql("fixture_admin", "CREATE TABLE public.existing (time timestamp, channels float[], id bigserial)")

        tasks = yaml.safe_load((REPO / "deploy/ansible/tasks/database.yml").read_text())
        block = copy.deepcopy(next(t for t in tasks if "block" in t))
        block.pop("become", None)
        block.pop("become_user", None)
        for defaults in block["module_defaults"].values():
            defaults["login_unix_socket"] = str(self.socket)
            defaults["connect_params"] = {"passfile": self.passfile}
        # TimescaleDB installation is separate; these are the actual database,
        # role and privilege tasks against real PostgreSQL measurement tables.
        block["block"] = [t for t in block["block"] if "community.postgresql.postgresql_ext" not in t]
        verification = yaml.safe_load((REPO / "deploy/ansible/tasks/verify.yml").read_text())
        acquisition_checks = [t for t in verification if t["name"] in (
            "Verify the acquisition account's read-and-append permissions",
            "Check that acquisition can read and append without modifying tables")]
        acquisition_checks[0]["community.postgresql.postgresql_query"]["connect_params"] = {
            "passfile": self.passfile, "sslrootcert": str(self.root / "server.crt")}
        self.playbook = self.root / "fixture.yml"
        self.play = {"hosts": "all", "gather_facts": False,
                     "vars": {**self.settings, "ansible_python_interpreter": sys.executable},
                     "tasks": [block, *acquisition_checks]}
        self.environment = {k: os.environ[k] for k in ("PATH", "LANG", "TMPDIR", "ANSIBLE_COLLECTIONS_PATH") if k in os.environ}
        self.environment.update(ANSIBLE_CONFIG=str(REPO / "deploy/ansible.cfg"),
                                ANSIBLE_HOME=str(self.root / "ansible-home"),
                                ANSIBLE_LOCAL_TEMP=str(self.root / "ansible-local"),
                                ANSIBLE_REMOTE_TEMP=str(self.root / "ansible-remote"), ANSIBLE_NOCOLOR="1")

    def run_command(self, command):
        result = subprocess.run(command, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def stop_database(self):
        subprocess.run([str(PG_BIN / "pg_ctl"), "-D", str(self.data), "-m", "immediate", "-w", "stop"],
                       capture_output=True, timeout=15)

    def connection(self, user, tcp=False, **options):
        params = {"host": "127.0.0.1" if tcp else str(self.socket),
                  "port": self.settings["ida_database_port"], "dbname": "fixture_measurements", "user": user,
                  "connect_timeout": 3, "passfile": self.passfile}
        if tcp:
            params.update(password=self.settings[f"ida_database_{user.removeprefix('fixture_')}_password"],
                          sslmode="require", sslrootcert=str(self.root / "server.crt"))
        params.update(options)
        conn = psycopg2.connect(**params)
        conn.autocommit = True
        return conn

    def sql(self, user, query, database="fixture_measurements"):
        conn = self.connection(user, dbname=database)
        try:
            with conn.cursor() as cursor:
                cursor.execute(query)
                return cursor.fetchall() if cursor.description else None
        finally:
            conn.close()

    def install(self):
        self.playbook.write_text(yaml.safe_dump([self.play]))
        result = subprocess.run([ANSIBLE, "-i", "localhost,", "-c", "local", str(self.playbook)],
                                env=self.environment, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_existing_and_future_tables_allow_reads_and_appends_but_not_modifications(self):
        self.install()
        self.sql("fixture_admin", "CREATE TABLE public.future (time timestamp, channels float[], id bigserial)")
        acquisition = self.connection("fixture_acquisition", tcp=True)
        reader = self.connection("fixture_reader", tcp=True)
        try:
            self.assertTrue(acquisition.info.ssl_in_use)
            with acquisition.cursor() as cursor:
                for table in ("existing", "future"):
                    cursor.execute(f"INSERT INTO public.{table} (time, channels) VALUES (now(), ARRAY[1.5]) RETURNING id")
                    self.assertEqual(cursor.fetchone(), (1,))
                    cursor.execute(f"SELECT channels FROM public.{table} WHERE id = 1")
                    self.assertEqual(cursor.fetchone(), ([1.5],))
                    for command in (f"DELETE FROM public.{table}",
                                    f"UPDATE public.{table} SET channels = ARRAY[2.5]", f"TRUNCATE public.{table}",
                                    f"DROP TABLE public.{table}", f"ALTER TABLE public.{table} ADD COLUMN extra int"):
                        with self.subTest(command=command):
                            with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                                cursor.execute(command)
                for command in ("CREATE TABLE public.forbidden (id int)", "CREATE ROLE forbidden"):
                    with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                        cursor.execute(command)
            with reader.cursor() as cursor:
                for table in ("existing", "future"):
                    cursor.execute(f"SELECT count(*), min(id) FROM public.{table}")
                    self.assertEqual(cursor.fetchone(), (1, 1))
                with self.assertRaises((psycopg2.errors.ReadOnlySqlTransaction, psycopg2.errors.InsufficientPrivilege)):
                    cursor.execute("INSERT INTO public.future (time, channels) VALUES (now(), ARRAY[2.5])")
        finally:
            acquisition.close()
            reader.close()
        self.install()  # Reapplying the installer retains permissions and data.
        self.assertEqual(self.sql("fixture_admin", "SELECT count(*) FROM public.future"), [(1,)])

    def test_admin_retains_full_control_over_measurement_tables_and_records(self):
        self.install()
        conn = self.connection("fixture_admin", tcp=True)
        try:
            with conn.cursor() as cursor:
                cursor.execute("CREATE TABLE public.admin_table (value int)")
                cursor.execute("INSERT INTO public.admin_table VALUES (1)")
                cursor.execute("UPDATE public.admin_table SET value = 2")
                cursor.execute("SELECT value FROM public.admin_table")
                self.assertEqual(cursor.fetchone(), (2,))
                cursor.execute("ALTER TABLE public.admin_table ADD COLUMN note text")
                cursor.execute("DELETE FROM public.admin_table")
                cursor.execute("SELECT count(*) FROM public.admin_table")
                self.assertEqual(cursor.fetchone(), (0,))
                cursor.execute("DROP TABLE public.admin_table")
                cursor.execute("SELECT to_regclass('public.admin_table')")
                self.assertEqual(cursor.fetchone(), (None,))
        finally:
            conn.close()

    def test_acquisition_requires_its_password_and_an_encrypted_tcp_connection(self):
        self.install()
        for options in ({"password": "incorrect-invented-password"}, {"sslmode": "disable"}):
            with self.subTest(options=options):
                with self.assertRaises(psycopg2.OperationalError):
                    self.connection("fixture_acquisition", tcp=True, **options)


if __name__ == "__main__":
    unittest.main()

"""Check synthetic measurements without credentials or a database connection."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import csv
from datetime import datetime, timedelta
import importlib.util
import io
from itertools import islice
import json
import math
from pathlib import Path
import random
from statistics import mean, pvariance
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


spec = importlib.util.spec_from_file_location(
    "seed_demo", Path(__file__).resolve().parents[1] / "seed-demo.py"
)
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)


class SeedDatabaseTests(unittest.TestCase):
    """Exercise seeding and recovery with a fake driver, never a live database."""

    def setUp(self):
        class DatabaseError(Exception):
            diag = SimpleNamespace(message_primary="Synthetic database failure")

        class DuplicateTable(DatabaseError):
            pass

        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.driver = SimpleNamespace(
            connect=MagicMock(return_value=self.connection), Error=DatabaseError,
            errors=SimpleNamespace(DuplicateTable=DuplicateTable),
            sql=SimpleNamespace(SQL=str, Identifier=lambda *parts: ".".join('"' + p + '"' for p in parts)),
        )
        self.settings = {"ida_database_port": 5432, "ida_database_name": "fixture",
                         "ida_database_admin": "fixture_admin", "ida_database_reader": "fixture_reader",
                         "ida_database_admin_password": "synthetic-password"}
        self.pulses = [(seed.DEFAULT_START + timedelta(seconds=n, microseconds=500_000),
                        (0.3, 650), 500_000_000_000) for n in (0, 1)]
        self.pressure_summary = (3, seed.DEFAULT_START, seed.DEFAULT_START + timedelta(seconds=2))
        self.pulse_summary = (2, self.pulses[0][0], self.pulses[-1][0])
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {"psycopg2": self.driver}).start()
        patch.object(seed, "SAMPLE_COUNT", 3).start()
        self.pulse_samples = patch.object(seed, "pulse_samples", return_value=iter(self.pulses)).start()

    def test_fresh_install_creates_both_tables_and_grants_viewer_access(self):
        self.cursor.fetchone.side_effect = [(None,), self.pressure_summary, (None,), self.pulse_summary]
        counts = seed.seed_database(self.settings, seed.DEFAULT_START, "all", if_missing=True)
        self.assertEqual(counts, {"pressure": 3, "pulses": 2})
        self.assertEqual(self.cursor.copy_expert.call_count, 2)
        queries = [str(call.args[0]) for call in self.cursor.execute.call_args_list]
        self.assertEqual(sum("CREATE TABLE" in query for query in queries), 2)
        self.assertEqual(sum("GRANT SELECT" in query for query in queries), 2)
        lookups = [call.args[1] for call in self.cursor.execute.call_args_list if call.args[0] == "SELECT to_regclass(%s)"]
        self.assertEqual(lookups, [("public.example_pressure",), ("public.example_pulses",)])
        self.connection.close.assert_called_once()

    def test_existing_pressure_table_is_not_written_while_missing_pulses_are_created(self):
        self.cursor.fetchone.side_effect = [("public.example_pressure",), (None,), self.pulse_summary]
        with patch.object(seed, "pressure_samples", side_effect=AssertionError("Existing pressure table touched")):
            counts = seed.seed_database(self.settings, seed.DEFAULT_START, "all", if_missing=True)
        self.assertEqual(counts, {"pressure": None, "pulses": 2})
        self.assertEqual(self.cursor.copy_expert.call_count, 1)
        writes = [str(call.args[0]) for call in self.cursor.execute.call_args_list
                  if "CREATE TABLE" in str(call.args[0]) or "GRANT SELECT" in str(call.args[0])]
        self.assertTrue(all('"example_pulses"' in query for query in writes))

    def test_existing_tables_are_preserved_without_generating_or_inserting_records(self):
        self.cursor.fetchone.side_effect = [("public.example_pressure",), ("public.example_pulses",)]
        with patch.object(seed, "pressure_samples", side_effect=AssertionError("Existing table touched")):
            counts = seed.seed_database(self.settings, seed.DEFAULT_START, "all", if_missing=True)
        self.assertEqual(counts, {"pressure": None, "pulses": None})
        self.cursor.copy_expert.assert_not_called()
        self.pulse_samples.assert_not_called()
        self.assertTrue(all(call.args[0] == "SELECT to_regclass(%s)" for call in self.cursor.execute.call_args_list))

    def test_insertion_failure_exits_the_transaction_with_an_error_and_closes_connection(self):
        self.cursor.fetchone.side_effect = [(None,), self.pressure_summary, (None,)]
        self.cursor.copy_expert.side_effect = [None, self.driver.Error()]
        with self.assertRaisesRegex(RuntimeError, "changes were rolled back"):
            seed.seed_database(self.settings, seed.DEFAULT_START, "all", if_missing=True)
        self.assertIs(self.connection.__exit__.call_args.args[0], self.driver.Error)
        self.connection.close.assert_called_once()

    def test_manual_seeding_still_refuses_an_existing_table(self):
        self.cursor.execute.side_effect = self.driver.errors.DuplicateTable()
        with self.assertRaisesRegex(ValueError, "already exists; no data was changed"):
            seed.seed_database(self.settings, seed.DEFAULT_START, "all")
        self.assertIn("CREATE TABLE", self.cursor.execute.call_args.args[0])
        self.cursor.copy_expert.assert_not_called()
        self.connection.close.assert_called_once()


class PressureDataTests(unittest.TestCase):
    def test_six_days_of_finite_nonnegative_readings_exactly_one_second_apart(self):
        previous = seed.DEFAULT_START - timedelta(seconds=1)
        count = 0
        peak = 0
        for timestamp, pressure in seed.pressure_samples():
            self.assertEqual(timestamp - previous, timedelta(seconds=1))
            self.assertTrue(math.isfinite(pressure))
            self.assertGreaterEqual(pressure, 0)
            peak = max(peak, pressure)
            previous = timestamp
            count += 1
        self.assertEqual(count, 518_400)
        self.assertEqual(previous, seed.DEFAULT_START + timedelta(days=6) - timedelta(seconds=1))
        self.assertGreater(peak, 4.4)
        self.assertLess(peak, 4.6)

    def test_profile_has_the_requested_rise_drop_plateau_and_zero_tail(self):
        self.assertAlmostEqual(seed.pressure_at(3600), 2.0)
        self.assertGreater(seed.pressure_at(5 * 3600), 4.3)
        self.assertLess(seed.pressure_at(12 * 3600), 3.1)
        self.assertGreater(seed.pressure_at(36 * 3600), 2.8)
        self.assertLess(seed.pressure_at(36 * 3600), 3.0)
        self.assertEqual(seed.pressure_at(5 * 24 * 3600), 0)
        last = list(islice(seed.pressure_samples(), 5 * 24 * 3600, None))
        self.assertTrue(all(pressure == 0 for _, pressure in last))

    def test_repeatable_values_and_shiftable_start_time(self):
        first = list(islice(seed.pressure_samples(), 100))
        self.assertEqual(first, list(islice(seed.pressure_samples(), 100)))
        shifted = list(islice(seed.pressure_samples(seed.DEFAULT_START + timedelta(days=10)), 100))
        for original, other in zip(first, shifted):
            self.assertEqual(other[0] - original[0], timedelta(days=10))
            self.assertEqual(other[1], original[1])

    def test_csv_contains_one_element_arrays_and_whole_second_timestamps(self):
        stream = io.StringIO()
        samples = list(islice(seed.pressure_samples(), 3))
        seed.write_csv(stream, samples, header=True)
        rows = list(csv.DictReader(io.StringIO(stream.getvalue())))
        self.assertEqual(len(rows), 3)
        for row, sample in zip(rows, samples):
            self.assertEqual(datetime.fromisoformat(row["time"]), sample[0])
            self.assertTrue(row["channels"].startswith("{") and row["channels"].endswith("}"))
            self.assertEqual(float(row["channels"][1:-1]), sample[1])

    def test_start_requires_whole_seconds_without_timezone_and_room_for_six_days(self):
        self.assertEqual(seed.parse_start("2026-02-01 12:34:56"), datetime(2026, 2, 1, 12, 34, 56))
        for value in ("not a date", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00.5", "9999-12-30", "0001-01-01"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                seed.parse_start(value)

    def test_csv_export_preserves_an_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.csv"
            path.write_text("existing measurements\n")
            for dataset in seed.TABLE_NAMES:
                with self.subTest(dataset=dataset):
                    with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                        seed.main(["--dataset", dataset, "--csv", str(path)])
                    self.assertEqual(result.exception.code, 1)
                    self.assertEqual(path.read_text(), "existing measurements\n")


class PulseDataTests(unittest.TestCase):
    def test_pulse_records_cover_the_pressure_window_with_consistent_precise_timestamps(self):
        previous = seed.DEFAULT_START
        stop = seed.DEFAULT_START + timedelta(days=6)
        count = 0
        fractional = 0
        for timestamp, (psp, energy), ps in seed.pulse_samples():
            self.assertGreaterEqual(timestamp, previous)
            self.assertLess(timestamp, stop)
            self.assertGreater(psp, 0)
            self.assertLess(psp, 1)
            self.assertIsInstance(energy, int)
            self.assertGreaterEqual(energy, 50)
            self.assertLess(energy, 4000)
            self.assertGreaterEqual(ps, 0)
            self.assertLess(ps, 1_000_000_000_000)
            self.assertEqual(timestamp.microsecond, ps // 1_000_000)
            fractional += timestamp.microsecond != 0
            previous = timestamp
            count += 1
        expected = (seed.GAMMA_BACKGROUND_RATE + seed.NEUTRON_BACKGROUND_RATE) * seed.SAMPLE_COUNT
        expected += seed.NEUTRON_BACKGROUND_RATE * 19 * 3 * 300
        self.assertLess(abs(count - expected), 6 * math.sqrt(expected))
        self.assertGreater(fractional, count * 0.99)

    def test_poisson_counts_and_exponential_waits_in_a_constant_rate_interval(self):
        offsets = list(seed.poisson_arrivals(0.5, 0, 20_000, random.Random(501)))
        waits = [offsets[0]] + [b - a for a, b in zip(offsets, offsets[1:])]
        self.assertAlmostEqual(mean(waits), 2, delta=0.1)
        self.assertAlmostEqual(pvariance(waits), 4, delta=0.4)
        counts = [0] * 1000
        for offset in offsets:
            counts[int(offset // 20)] += 1
        self.assertAlmostEqual(mean(counts), 10, delta=0.5)
        self.assertAlmostEqual(pvariance(counts) / mean(counts), 1, delta=0.2)

    def test_three_five_minute_bursts_raise_only_neutron_rate_twentyfold(self):
        self.assertEqual(len(seed.BURST_STARTS), 3)
        self.assertEqual(seed.BURST_DURATION, 300)
        self.assertEqual(seed.BURST_MULTIPLIER, 20)
        background = {"gamma": 0, "neutron": 0}
        bursts = [{"gamma": 0, "neutron": 0} for _ in seed.BURST_STARTS]
        for offset, population in seed.pulse_arrivals():
            for i, begin in enumerate(seed.BURST_STARTS):
                if begin <= offset < begin + 300:
                    bursts[i][population] += 1
                    break
            else:
                background[population] += 1
        for counts in bursts:
            for population, expected in (("neutron", 0.035 * 20 * 300), ("gamma", 0.35 * 300)):
                self.assertLess(abs(counts[population] - expected), 6 * math.sqrt(expected))
        duration = seed.SAMPLE_COUNT - 3 * 300
        self.assertAlmostEqual(background["neutron"] / duration, 0.035, delta=0.002)
        self.assertAlmostEqual(background["gamma"] / background["neutron"], 10, delta=0.5)

    def test_gamma_arrivals_are_identical_with_or_without_neutron_bursts(self):
        gammas = [offset for offset, population in seed.pulse_arrivals() if population == "gamma"]
        with patch.object(seed, "BURST_STARTS", ()):
            without = [offset for offset, population in seed.pulse_arrivals() if population == "gamma"]
        self.assertEqual(gammas, without)

    def test_burst_energies_are_above_500_and_background_records_are_preserved(self):
        ordinary = {"gamma": random.Random(103), "neutron": random.Random(104)}
        bursts, background = [], []
        for (offset, population), (_, channels, _) in zip(seed.pulse_arrivals(), seed.pulse_samples()):
            original = seed.pulse_channels(population, ordinary[population])
            if population == "neutron" and seed.in_neutron_burst(offset):
                self.assertGreater(channels[1], 500)
                bursts.append(channels[1])
            else:
                self.assertEqual(channels, original)
                if population == "neutron":
                    background.append(channels[1])
        self.assertGreater(len(bursts), 500)
        self.assertGreater(mean(bursts), 2 * mean(background))

    def test_psd_has_two_gaussian_bands_narrowing_with_energy(self):
        for population in ("gamma", "neutron"):
            rng = random.Random(502)
            values = [seed.pulse_channels(population, rng) for _ in range(20_000)]
            low = [psp for psp, energy in values if 80 <= energy <= 120]
            high = [psp for psp, energy in values if 1500 <= energy <= 2500]
            self.assertGreater(len(low), 500)
            self.assertGreater(len(high), 500)
            self.assertGreater(pvariance(low), pvariance(high))
            if population == "gamma":
                self.assertAlmostEqual(mean(low), 0.15, delta=0.01)
                self.assertLess(mean(psp > 0.22 for psp, _ in values), 0.005)
            else:
                self.assertAlmostEqual(mean(low), 0.31, delta=0.02)
                self.assertGreater(mean(psp > 0.22 for psp, _ in values), 0.95)
            self.assertGreater(mean(energy < 500 for _, energy in values), 0.4)

    def test_csv_array_order_precision_and_reproducibility(self):
        samples = list(islice(seed.pulse_samples(), 100))
        self.assertEqual(samples, list(islice(seed.pulse_samples(), 100)))
        shifted = list(islice(seed.pulse_samples(seed.DEFAULT_START + timedelta(days=1)), 100))
        for original, other in zip(samples, shifted):
            self.assertEqual(other[0] - original[0], timedelta(days=1))
            self.assertEqual(other[1:], original[1:])
        stream = io.StringIO()
        self.assertEqual(seed.write_pulse_csv(stream, samples, header=True), 100)
        for row, sample in zip(csv.DictReader(io.StringIO(stream.getvalue())), samples):
            psp, energy = row["channels"].strip("{}").split(",")
            self.assertEqual((float(psp), int(energy)), sample[1])
            self.assertEqual(datetime.fromisoformat(row["time"]), sample[0])
            self.assertEqual(int(row["ps"]), sample[2])


class ExamplePanelTests(unittest.TestCase):
    def test_panel_filters_and_devices_match_the_generated_data(self):
        graphs = seed.example_panel(seed.DEFAULT_START)["graphs"]
        selections = [
            [(band["device"], band["filters"], band["granularity"]) for band in graph["bands"]]
            for graph in graphs
        ]
        self.assertEqual(selections, [
            [("example_pressure", "singlechannel(1)", "60")],
            [("example_pulses", "cpm(<0.22,>0)", "1")],
            [("example_pulses", "cpm(>0.22,>500)", "1")],
            [("example_pulses", "spectrum(<0.22,>0)", "1"),
             ("example_pulses", "spectrum(>0.22,>500)", "1")],
        ])

    def test_all_graphs_inherit_the_complete_shifted_data_window(self):
        start = datetime(2026, 2, 1, 12, 34, 56)
        panel = seed.example_panel(start)
        self.assertLess(datetime.fromisoformat(panel["start"]), start)
        self.assertEqual(datetime.fromisoformat(panel["end"]), start + timedelta(days=6))
        for graph in panel["graphs"]:
            for item in [graph, *graph["bands"]]:
                self.assertFalse(item.get("start"))
                self.assertFalse(item.get("end"))

    def test_new_panel_is_complete_writable_and_preserves_subsequent_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "json-files" / "example-panel.json"
            self.assertTrue(seed.write_example_panel(seed.DEFAULT_START, destination))
            self.assertEqual(json.loads(destination.read_text()), seed.example_panel(seed.DEFAULT_START))
            self.assertTrue(destination.stat().st_mode & 0o020)  # Apache group can save edits.
            destination.write_text('{"header": "User edited this panel"}\n')
            self.assertFalse(seed.write_example_panel(seed.DEFAULT_START, destination))
            self.assertEqual(destination.read_text(), '{"header": "User edited this panel"}\n')
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_failed_publication_leaves_no_partial_panel_or_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "example-panel.json"
            with patch.object(seed.os, "link", side_effect=OSError("Simulated full disk")):
                with self.assertRaises(OSError):
                    seed.write_example_panel(seed.DEFAULT_START, destination)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_panel_only_does_not_read_settings_or_connect_to_the_database(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "example-panel.json"
            with (patch.object(seed, "DEFAULT_PANEL", destination),
                  patch.object(seed, "load_settings", side_effect=AssertionError("Settings accessed")),
                  patch.object(seed, "seed_database", side_effect=AssertionError("Database accessed")),
                  redirect_stdout(io.StringIO())):
                seed.main(["--panel-only", "--start", "2026-02-01 00:00:00"])
            panel = json.loads(destination.read_text())
            self.assertEqual(panel["start"], "2026-01-31T23:59:59")
            self.assertEqual(panel["end"], "2026-02-07T00:00:00")

    def test_combined_seeding_creates_the_panel_after_database_success(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "example-panel.json"
            with (patch.object(seed, "DEFAULT_PANEL", destination),
                  patch.object(seed, "load_settings", return_value={}),
                  patch.object(seed, "seed_database", return_value={"pressure": 518_400, "pulses": 200_559}) as database,
                  redirect_stdout(io.StringIO())):
                seed.main(["--dataset", "all"])
            database.assert_called_once_with({}, seed.DEFAULT_START, "all", if_missing=False)
            self.assertTrue(destination.exists())

    def test_installer_mode_preserves_existing_tables_and_creates_only_a_missing_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "example-panel.json"
            with (patch.object(seed, "DEFAULT_PANEL", destination),
                  patch.object(seed, "load_settings", return_value={}) as settings,
                  patch.object(seed, "seed_database", return_value={"pressure": None, "pulses": None}) as database,
                  redirect_stdout(io.StringIO())):
                seed.main(["--dataset", "all", "--if-missing", "--settings", "invented-settings.yml"])
                settings.assert_called_once_with(Path("invented-settings.yml"))
                database.assert_called_once_with({}, seed.DEFAULT_START, "all", if_missing=True)
                self.assertTrue(destination.exists())
                destination.write_text('{"header":"User edited this panel"}')
                seed.main(["--dataset", "all", "--if-missing"])
            self.assertEqual(destination.read_text(), '{"header":"User edited this panel"}')

    def test_installer_can_retry_panel_publication_after_database_seeding_succeeded(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "example-panel.json"
            with (patch.object(seed, "DEFAULT_PANEL", destination),
                  patch.object(seed, "load_settings", return_value={}),
                  patch.object(seed, "seed_database", side_effect=[{"pressure": 518_400, "pulses": 200_559},
                                                                   {"pressure": None, "pulses": None}]),
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
                with patch.object(seed, "write_example_panel", side_effect=OSError("Synthetic disk full")):
                    with self.assertRaises(SystemExit) as result:
                        seed.main(["--dataset", "all", "--if-missing"])
                    self.assertEqual(result.exception.code, 1)
                self.assertFalse(destination.exists())
                seed.main(["--dataset", "all", "--if-missing"])
                self.assertTrue(destination.exists())

    def test_database_failure_does_not_create_a_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "example-panel.json"
            with (patch.object(seed, "DEFAULT_PANEL", destination),
                  patch.object(seed, "load_settings", return_value={}),
                  patch.object(seed, "seed_database", side_effect=ValueError("Table already exists")),
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
                  self.assertRaises(SystemExit) as result):
                seed.main(["--dataset", "all"])
            self.assertEqual(result.exception.code, 1)
            self.assertFalse(destination.exists())

    def test_csv_options_cannot_accidentally_seed_a_database_or_create_a_panel(self):
        for options in (["--dataset", "all"], ["--panel-only"], ["--if-missing"]):
            with self.subTest(options=options), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                seed.main([*options, "--csv", "unused.csv"])
            self.assertEqual(result.exception.code, 2)

    def test_panel_only_rejects_installer_database_mode(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
            seed.main(["--panel-only", "--if-missing"])
        self.assertEqual(result.exception.code, 2)


if __name__ == "__main__":
    unittest.main()

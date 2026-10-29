#!/usr/bin/env python3
"""Generate synthetic pressure and gamma/neutron pulses with an example panel.

The Ubuntu installer runs this automatically with --dataset all --if-missing
and its private --settings file. Existing tables and panel edits are preserved.
For manual use, --dataset all creates both tables and the panel; --start shifts
the six-day window. --csv exports one dataset without database access, and
--panel-only creates just the panel. Without --if-missing, existing tables are
an error so a manual seed cannot accidentally append duplicate measurements.
"""

import argparse
import csv
from datetime import datetime, timedelta
from heapq import merge
import io
from itertools import islice
import json
import math
import os
from pathlib import Path
import random
import tempfile


DEFAULT_START = datetime(2026, 1, 1)
SAMPLE_COUNT = 6 * 24 * 60 * 60
BATCH_SIZE = 10_000
TABLE_NAMES = {"pressure": "example_pressure", "pulses": "example_pulses"}
DEFAULT_PANEL = Path(__file__).resolve().parents[1] / "json-files" / "example-panel"

# Approximate neutron-selected background from Radiation-pulse-analysis.ipynb:
# 2.1 events/minute before an energy cut. Gamma intensity is the illustrative
# 10:1 ratio, rather than a fit to that detector's full gamma population.
NEUTRON_BACKGROUND_RATE = 0.035  # events/second, before PSP/energy selection
GAMMA_BACKGROUND_RATE = 10 * NEUTRON_BACKGROUND_RATE
BURST_MULTIPLIER = 20           # total neutron rate during a burst
BURST_DURATION = 5 * 60         # seconds
BURST_STARTS = (24 * 3600, 60 * 3600, 108 * 3600)  # elapsed seconds
BURST_MIN_ENERGY = 501
BURST_ENERGY_SCALE = 900

# Elapsed hours and pressure in bar. These describe an illustrative profile,
# not a physical absorption model or a copy of experimental measurements.
PRESSURE_PROFILE = (
    (0, 2.0),
    (2, 2.0),
    (4.5, 4.45),
    (5, 4.4),
    (8, 4.32),
    (11, 4.05),
    (12, 2.95),
    (20, 2.93),
    (60, 2.85),
    (69, 2.85),
    (70, 1.1),
    (72, 0.2),
    (76, 0.0),
    (144, 0.0),
)


def pressure_at(elapsed_seconds):
    """Interpolate smoothly between the pressure-profile control points."""
    hours = elapsed_seconds / 3600
    for (start, first), (end, last) in zip(PRESSURE_PROFILE, PRESSURE_PROFILE[1:]):
        if hours <= end:
            fraction = (hours - start) / (end - start)
            smooth = fraction * fraction * (3 - 2 * fraction)
            return first + (last - first) * smooth
    return PRESSURE_PROFILE[-1][1]


def pressure_samples(start=DEFAULT_START):
    """Yield evenly spaced timestamps and repeatable, slightly noisy pressures."""
    rng = random.Random(42)
    for second in range(SAMPLE_COUNT):
        pressure = pressure_at(second)
        # Taper the measurement noise near zero; the final zero plateau is exact.
        noise = rng.gauss(0, 0.003) * min(1, pressure / 0.05)
        yield start + timedelta(seconds=second), round(max(0, pressure + noise), 6)


def write_csv(stream, samples, header=False):
    """Use PostgreSQL's array notation so the same CSV format works for COPY."""
    writer = csv.writer(stream, lineterminator="\n")
    if header:
        writer.writerow(("time", "channels"))
    for timestamp, pressure in samples:
        writer.writerow((timestamp.isoformat(sep=" "), "{" + f"{pressure:.6f}" + "}"))


def poisson_arrivals(rate, start, stop, rng):
    """Generate a Poisson process via exponential waits, stopping at the boundary."""
    elapsed = start + rng.expovariate(rate)
    while elapsed < stop:
        yield elapsed
        elapsed += rng.expovariate(rate)


def neutron_arrivals():
    """Use independent Poisson increments in each constant-rate time interval."""
    rng = random.Random(101)
    previous = 0
    for burst_start in BURST_STARTS:
        yield from poisson_arrivals(NEUTRON_BACKGROUND_RATE, previous, burst_start, rng)
        burst_end = burst_start + BURST_DURATION
        yield from poisson_arrivals(
            NEUTRON_BACKGROUND_RATE * BURST_MULTIPLIER, burst_start, burst_end, rng
        )
        previous = burst_end
    yield from poisson_arrivals(NEUTRON_BACKGROUND_RATE, previous, SAMPLE_COUNT, rng)


def pulse_arrivals():
    """Merge gamma and neutron events in time order; labels stay out of the table."""
    gammas = (
        (elapsed, "gamma")
        for elapsed in poisson_arrivals(GAMMA_BACKGROUND_RATE, 0, SAMPLE_COUNT, random.Random(102))
    )
    neutrons = ((elapsed, "neutron") for elapsed in neutron_arrivals())
    yield from merge(gammas, neutrons)


def in_neutron_burst(elapsed):
    return any(begin <= elapsed < begin + BURST_DURATION for begin in BURST_STARTS)


def pulse_channels(population, rng, burst=False):
    """Sample [PSP, energy] with two energy-dependent Gaussian PSP bands."""
    # These are uncalibrated digitizer values, not keV. Reject the upper tail
    # rather than piling clipped events up at the end of the energy range.
    minimum = BURST_MIN_ENERGY if burst else 50
    scale = BURST_ENERGY_SCALE if burst else (700 if population == "gamma" else 500)
    while True:
        energy = int(minimum + rng.expovariate(1 / scale))
        if energy < 4000:
            break
    if population == "gamma":
        mean = 0.15 + 0.035 * (1 - math.exp(-(energy - 50) / 800))
        sigma = 0.005 + 0.012 * (100 / energy) ** 0.7
    else:
        mean = 0.22 + 0.095 * math.exp(-(energy - 50) / 900)
        sigma = 0.008 + 0.020 * math.sqrt(100 / energy)
    while True:
        psp = rng.gauss(mean, sigma)
        if 0 < psp < 1:
            return round(psp, 6), energy


def pulse_samples(start=DEFAULT_START):
    """Yield irregular pulse timestamps, [PSP, energy], and subsecond picoseconds."""
    # Independent streams keep the timing unaffected by the PSD model and let
    # the neutron bursts leave the complete gamma sequence unchanged.
    attributes = {"gamma": random.Random(103), "neutron": random.Random(104)}
    burst_attributes = random.Random(105)
    for elapsed, population in pulse_arrivals():
        seconds, ps = divmod(int(elapsed * 1_000_000_000_000), 1_000_000_000_000)
        timestamp = start + timedelta(seconds=seconds, microseconds=ps // 1_000_000)
        channels = pulse_channels(population, attributes[population])
        if population == "neutron" and in_neutron_burst(elapsed):
            # Consume the ordinary draw above too, so all subsequent background
            # records retain the same values when the burst model changes.
            channels = pulse_channels("neutron", burst_attributes, burst=True)
        yield timestamp, channels, ps


def write_pulse_csv(stream, samples, header=False):
    writer = csv.writer(stream, lineterminator="\n")
    if header:
        writer.writerow(("time", "channels", "ps"))
    count = 0
    for timestamp, (psp, energy), ps in samples:
        writer.writerow((timestamp.isoformat(sep=" "), f"{{{psp:.6f},{energy}}}", ps))
        count += 1
    return count


def example_panel(start):
    template = Path(__file__).resolve().with_name("example-panel.json")
    panel = json.loads(template.read_text())
    # Graph queries exclude exact boundaries; include the first pressure sample.
    panel["start"] = (start - timedelta(seconds=1)).isoformat()
    panel["end"] = (start + timedelta(seconds=SAMPLE_COUNT)).isoformat()
    return panel


def write_example_panel(start, destination=None):
    """Publish a complete panel while preserving any existing panel edits."""
    destination = DEFAULT_PANEL if destination is None else destination
    if destination.exists():
        return False
    panel = example_panel(start)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".example-panel-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(panel, stream, indent=4)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Restore the inherited group/ACL write access for Apache's editor.
        os.chmod(temporary, 0o664)
        try:
            os.link(temporary, destination)  # Atomic publication without replacement.
        except FileExistsError:
            return False
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def load_settings(path):
    # CSV generation needs only the standard library. Database dependencies are
    # loaded only for insertion; the Ubuntu installer supplies these packages.
    try:
        import yaml
    except ImportError:
        raise RuntimeError("Database seeding requires PyYAML (Ubuntu package python3-yaml).") from None
    try:
        with path.open() as stream:
            settings = yaml.safe_load(stream)
    except yaml.YAMLError:
        # YAML parser messages can include source lines containing passwords.
        raise ValueError("The private deployment settings are not valid YAML.") from None
    required = (
        "ida_database_name", "ida_database_admin",
        "ida_database_admin_password", "ida_database_reader",
    )
    if not isinstance(settings, dict) or any(
        not isinstance(settings.get(key), str) or not settings[key] for key in required
    ):
        raise ValueError("The settings file must define the database name, owner, owner password and viewer account.")
    port = settings.get("ida_database_port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("ida_database_port must be an integer between 1 and 65535.")
    return settings


def seed_database(settings, start, dataset="pressure", *, if_missing=False):
    """Create new example tables, committing all selected datasets together."""
    datasets = tuple(TABLE_NAMES) if dataset == "all" else (dataset,)
    table_name = TABLE_NAMES[datasets[0]]
    counts = {}
    try:
        import psycopg2
        from psycopg2 import sql
    except ImportError:
        raise RuntimeError("Database seeding requires psycopg2 (Ubuntu package python3-psycopg2).") from None

    try:
        connection = psycopg2.connect(
            host="127.0.0.1", port=settings["ida_database_port"],
            dbname=settings["ida_database_name"], user=settings["ida_database_admin"],
            password=settings["ida_database_admin_password"], connect_timeout=10,
        )
    except psycopg2.Error:
        raise RuntimeError("Cannot connect to the local database. Check the deployment settings and PostgreSQL service.") from None

    try:
        # PostgreSQL rolls back the table, hypertable and rows together on error.
        # Skip whole existing tables in installer mode. CREATE TABLE still has
        # no IF NOT EXISTS, so a concurrent creation cannot receive our rows.
        with connection:
            with connection.cursor() as cursor:
                for name in datasets:
                    table_name = TABLE_NAMES[name]
                    if if_missing:
                        cursor.execute("SELECT to_regclass(%s)", ("public." + table_name,))
                        if cursor.fetchone()[0] is not None:
                            counts[name] = None
                            continue
                    samples = pressure_samples(start) if name == "pressure" else pulse_samples(start)
                    write_batch = write_csv if name == "pressure" else write_pulse_csv
                    table = sql.Identifier("public", table_name)
                    cursor.execute(sql.SQL("""
                        CREATE TABLE {} (
                            time TIMESTAMP NOT NULL,
                            channels FLOAT[] NOT NULL,
                            {}
                            id BIGSERIAL
                        )
                    """).format(table, sql.SQL("ps BIGINT," if name == "pulses" else "")))
                    cursor.execute(
                        "SELECT create_hypertable(%s, %s)",
                        ("public." + table_name, "time"),
                    )
                    columns = sql.SQL("time, channels, ps" if name == "pulses" else "time, channels")
                    copy_query = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT CSV)").format(table, columns)
                    count, first, last = 0, None, None
                    while batch := list(islice(samples, BATCH_SIZE)):
                        buffer = io.StringIO()
                        write_batch(buffer, batch)
                        buffer.seek(0)
                        cursor.copy_expert(copy_query, buffer)
                        if first is None:
                            first = batch[0][0]
                        last = batch[-1][0]
                        count += len(batch)
                    cursor.execute(
                        sql.SQL("GRANT SELECT ON {} TO {}").format(
                            table, sql.Identifier(settings["ida_database_reader"])
                        )
                    )
                    cursor.execute(sql.SQL("SELECT COUNT(*), MIN(time), MAX(time) FROM {}").format(table))
                    expected = (count, first, last)
                    if name == "pressure":
                        expected = (SAMPLE_COUNT, start, start + timedelta(seconds=SAMPLE_COUNT - 1))
                    if cursor.fetchone() != expected:
                        raise RuntimeError("The inserted records did not match the expected count and time range.")
                    counts[name] = count
    except psycopg2.errors.DuplicateTable:
        raise ValueError(f"public.{table_name} already exists; no data was changed.") from None
    except psycopg2.Error as error:
        detail = error.diag.message_primary or "Check the PostgreSQL and TimescaleDB configuration."
        raise RuntimeError(f"Database seeding failed; changes were rolled back. {detail}") from None
    finally:
        connection.close()
    return counts if dataset == "all" else counts[dataset]


def parse_start(value):
    try:
        start = datetime.fromisoformat(value)
        if start.tzinfo is not None or start.microsecond:
            raise ValueError
        # Check that the complete six-day range is representable too.
        start + timedelta(seconds=SAMPLE_COUNT)
        start - timedelta(seconds=1)
        return start
    except (ValueError, OverflowError):
        raise argparse.ArgumentTypeError("Use a timestamp such as '2026-01-01 00:00:00', without a timezone or fractional seconds.") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", choices=(*TABLE_NAMES, "all"), default="pressure",
        help="Dataset to generate (default: pressure). 'all' seeds both tables and creates the example panel.",
    )
    parser.add_argument(
        "--settings", type=Path, default=Path(__file__).resolve().with_name("settings.yml"),
        help="Private deployment settings, used for database insertion (default: deploy/settings.yml).",
    )
    parser.add_argument("--start", type=parse_start, default=DEFAULT_START, help="First sample timestamp (default: 2026-01-01 00:00:00).")
    parser.add_argument("--csv", type=Path, help="Write a new CSV file instead of connecting to a database.")
    parser.add_argument("--panel-only", action="store_true", help="Create example panel JSON without accessing a database.")
    parser.add_argument("--if-missing", action="store_true", help="Preserve existing tables when resuming installation.")
    args = parser.parse_args(argv)
    if args.csv and (args.panel_only or args.dataset == "all"):
        parser.error("Use --csv with one dataset (pressure or pulses), separately from --panel-only.")
    if args.if_missing and (args.csv or args.panel_only):
        parser.error("Use --if-missing only when inserting datasets into the database.")

    try:
        if args.panel_only:
            created = write_example_panel(args.start)
            print(f"{'Created' if created else 'Preserved existing panel:'} {DEFAULT_PANEL}")
            print(f"Panel URL: load-panel.php?filename={DEFAULT_PANEL.name}")
            return
        elif args.csv:
            with args.csv.open("x", newline="") as stream:
                if args.dataset == "pressure":
                    write_csv(stream, pressure_samples(args.start), header=True)
                    count = SAMPLE_COUNT
                else:
                    count = write_pulse_csv(stream, pulse_samples(args.start), header=True)
            label = "pressure" if args.dataset == "pressure" else "pulse"
            print(f"Wrote {count:,} synthetic {label} records to {args.csv}.")
        else:
            settings = load_settings(args.settings)
            print("Creating example tables and inserting synthetic data…", flush=True)
            count = seed_database(settings, args.start, args.dataset, if_missing=args.if_missing)
            if args.dataset == "all":
                for name, total in count.items():
                    if total is None:
                        print(f"Preserved existing public.{TABLE_NAMES[name]}.")
                    else:
                        print(f"Inserted {total:,} records into public.{TABLE_NAMES[name]}; viewer SELECT access granted.")
                try:
                    created = write_example_panel(args.start)
                except OSError as error:
                    raise RuntimeError(
                        f"Example tables are available, but the panel could not be written: {error}. "
                        "Correct the file permissions, then rerun the installer or use --panel-only."
                    ) from None
                print(f"{'Created' if created else 'Preserved existing panel:'} {DEFAULT_PANEL}")
                print(f"Panel URL: load-panel.php?filename={DEFAULT_PANEL.name}")
            else:
                if count is None:
                    print(f"Preserved existing public.{TABLE_NAMES[args.dataset]}.")
                else:
                    print(f"Inserted {count:,} records into public.{TABLE_NAMES[args.dataset]}; viewer SELECT access granted.")
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")

    if args.dataset == "pressure":
        print(f"Time range: {args.start} to {args.start + timedelta(seconds=SAMPLE_COUNT - 1)} (one record per second).")
    else:
        print(f"Pulse window: {args.start} to {args.start + timedelta(seconds=SAMPLE_COUNT)} (random arrival times).")
        for offset in BURST_STARTS:
            begin = args.start + timedelta(seconds=offset)
            print(f"Neutron burst: {begin} to {begin + timedelta(seconds=BURST_DURATION)} ({BURST_MULTIPLIER}x total rate).")


if __name__ == "__main__":
    main()

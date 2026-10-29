import os
import sys
import json
import argparse
import pandas as pd
import psycopg2
from pathlib import Path
from datetime import datetime
import time
import re
import csv
from psql_credentials import PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
import shutil
from functools import reduce
from graph_generation import GenerationControl

# ---- Spectrum binning defaults (edit here if needed) ----
SPECTRUM_BIN_WIDTH = 4      # Energy bin width (channels)
SPECTRUM_ENERGY_MIN = 0     # Inclusive lower energy bound
SPECTRUM_ENERGY_MAX = 4096  # Inclusive upper energy bound

def normalize_sql(query: str) -> str:
    # Lowercase the whole query
    query = query.lower()
    # Replace all whitespace (tabs, multiple spaces, newlines) with single space
    query = re.sub(r'\s+', ' ', query)
    # Strip leading/trailing whitespace
    return query.strip()


def data_path(root: Path, path: Path) -> Path:
    # resolve() checks existing parent links even when the destination is new.
    try:
        root = root.resolve()
        target = (root / path).resolve()
    except (OSError, RuntimeError):
        raise ValueError("Invalid file path") from None
    if target == root or not target.is_relative_to(root):
        raise ValueError("Invalid file path")
    return target


# Argument parsing
parser = argparse.ArgumentParser()
parser.add_argument("json_filename", type=str)
parser.add_argument("graph_no", type=str)
parser.add_argument("debug", type=int)
parser.add_argument("--base-url", default="", help="Public viewer URL supplied by PHP for diagnostic links")
parser.add_argument("--cancel-fd", type=int, default=None, help=argparse.SUPPRESS)
args = parser.parse_args()
generation_control = GenerationControl(args.cancel_fd)

json_filename = args.json_filename
graph_no = args.graph_no
debug = args.debug

print(f"Using database: {PGDATABASE}")

# Resolve from this script, so data paths stay absolute regardless of the working directory.
apphome = Path(__file__).resolve().parent
tempfolder = (apphome / "json-files").resolve()
csvfolder = (apphome / "csv-files").resolve()

# Extract the directory structure and filename from json_filename
json_path = Path(json_filename)
if (json_path.is_absolute() or "\\" in json_filename or re.match(r"^[A-Za-z]:", json_filename)
        or ".." in json_path.parts or any(ord(c) < 32 or ord(c) == 127 for c in json_filename)
        or not json_path.name or json_path.name.startswith(".") or json_path.name.endswith(".")
        or json_path.suffix.lower() not in ("", ".json")):
    parser.error("Invalid panel file path")
if not re.fullmatch(r"[1-9][0-9]{0,18}", graph_no) or int(graph_no) > sys.maxsize:
    parser.error("Invalid graph number")
subfolder = json_path.parent
panel_name = json_path.stem
try:
    json_filepath = data_path(tempfolder, json_path)
    csv_output_dir = data_path(csvfolder, subfolder / panel_name)
    csv_filepath = data_path(csvfolder, csv_output_dir / f"{panel_name}-{graph_no}.csv")
    meta_filepath = data_path(csvfolder, csv_output_dir / f"{panel_name}-{graph_no}.meta.json")
    fullres_filepath = data_path(csvfolder, csv_output_dir / f"{panel_name}-{graph_no}-fullres.csv")
except ValueError:
    parser.error("Invalid file path")
csv_output_dir.mkdir(parents=True, exist_ok=True)

# ---- helper: extract times from SQL ----
def extract_start_end_time(query: str) -> tuple[str | None, str | None]:
    start_match = re.search(r"time\s*>\s*'([^']+)'", query, re.IGNORECASE)
    end_match = re.search(r"time\s*<\s*'([^']+)'", query, re.IGNORECASE)
    return (start_match.group(1) if start_match else None,
            end_match.group(1) if end_match else None)

# ---- helper: adjust start time in SQL ----
def adjust_query_start(query: str, new_start: str) -> str:
    return re.sub(r"(time\s*>\s*)'[^']+'", rf"\1'{new_start}'", query, flags=re.IGNORECASE)

def get_adjusted_time_range(band_start: str, band_end: str, snapshot_end: str | None) -> tuple[str, str]:
    """
    Returns adjusted (start, end) for a band, assuming:
    - All bands in current graph are time-based OR energy-based.
    - If time-based: always skip snapshot overlap.
    - If energy-based: only skip if snapshot_end overlaps.
    """
    if snapshot_end is None:
        return band_start, band_end

    def _parse_ts(value: str) -> pd.Timestamp | None:
        if not value:
            return None
        ts = pd.to_datetime(value, errors="coerce")
        if pd.isna(ts):
            return None
        if ts.tzinfo is not None:
            ts = ts.tz_convert(None)
        return ts

    band_start_ts = _parse_ts(band_start)
    snapshot_end_ts = _parse_ts(snapshot_end)
    band_end_ts = _parse_ts(band_end) if band_end else None

    if band_start_ts is None or snapshot_end_ts is None:
        # Fallback: best-effort string compare (ISO-ish timestamps compare lexicographically).
        adjusted_start = max(snapshot_end, band_start)
        if band_end and adjusted_start > band_end:
            adjusted_start = band_end
        return adjusted_start, band_end

    adjusted_start_ts = max(snapshot_end_ts, band_start_ts)
    if band_end_ts is not None and adjusted_start_ts >= band_end_ts:
        # Snapshot already covers (or extends past) the requested end.
        adjusted_start_ts = band_end_ts

    return adjusted_start_ts.strftime("%Y-%m-%d %H:%M:%S"), band_end

# ---- updated snapshot lookup ----
def find_snapshot_csv_partial(subfolder: Path, graph_id: str, current_queries: list[str]):
    base_folder = csvfolder / subfolder / panel_name
    if not base_folder.exists():
        return None, None, None, None

    normalized_current = set(normalize_sql(q) for q in current_queries)
    current_queries_info = [extract_start_end_time(q) for q in current_queries]

    # Sort folders by modification time descending
    snapshot_dirs = sorted(
        (p for p in base_folder.glob("*_*") if not p.is_symlink() and p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    for snapshot_dir in snapshot_dirs:
        meta_path = snapshot_dir / f"{panel_name}-{graph_id}.meta.json"
        csv_path = snapshot_dir / f"{panel_name}-{graph_id}.csv"
        fullres_filepath = snapshot_dir / f"{panel_name}-{graph_id}-fullres.csv"

        # Generated snapshot files are regular files; do not follow links to other data.
        if any(p.is_symlink() for p in (meta_path, csv_path, fullres_filepath)):
            continue
        if not (meta_path.exists() and csv_path.exists() and fullres_filepath.exists()):
            continue

        with open(meta_path) as f:
            meta = json.load(f)

        meta_queries = meta.get("queries", [])
        normalized_meta = set(normalize_sql(q) for q in meta_queries)

        if normalized_meta == normalized_current:
            return csv_path, fullres_filepath, snapshot_dir.name, None

        meta_info = [extract_start_end_time(q) for q in meta_queries]
        if len(meta_info) != len(current_queries_info):
            continue

        match_structure = all(
            normalize_sql(mq).split("time <")[0].strip() ==
            normalize_sql(cq).split("time <")[0].strip()
            for mq, cq in zip(meta_queries, current_queries)
        )
        match_start_time = all(
            m_start == c_start
            for (m_start, _), (c_start, _) in zip(meta_info, current_queries_info)
        )

        if match_structure and match_start_time:
            snapshot_end_time = meta_info[0][1]  # Assume same across bands
            return csv_path, fullres_filepath, snapshot_dir.name, snapshot_end_time

    return None, None, None, None


# Snapshot check
def find_snapshot_csv(subfolder: Path, graph_id: str, current_queries: list[str]):
    base_folder = csvfolder / subfolder / panel_name
    if not base_folder.exists():
        return None, None, None  # Return three items

    normalized_current = set(normalize_sql(q) for q in current_queries)

    for snapshot_dir in sorted(base_folder.glob("*_*")):
        if snapshot_dir.is_symlink() or not snapshot_dir.is_dir():
            continue
        meta_path = snapshot_dir / f"{panel_name}-{graph_id}.meta.json"
        csv_path = snapshot_dir / f"{panel_name}-{graph_id}.csv"
        fullres_filepath = snapshot_dir / f"{panel_name}-{graph_id}-fullres.csv"

        if any(p.is_symlink() for p in (meta_path, csv_path, fullres_filepath)):
            continue
        if meta_path.exists() and csv_path.exists() and fullres_filepath.exists():
            with open(meta_path) as f:
                meta = json.load(f)
                meta_queries = meta.get("queries", [])
                normalized_meta = set(normalize_sql(q) for q in meta_queries)

                if normalized_meta == normalized_current:
                    return csv_path, allbands_info, snapshot_dir.name

    return None, None, None


def parse_filters(filters):
    filters_list = []
    pattern = re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)(?:\(([^()]*)\))?', filters)
    for name, params in pattern:
        param_list = [p.strip() for p in params.split(',')] if params else []
        filters_list.append({"name": name, "params": param_list})
    return filters_list

# Connect to PostgreSQL
def connect_to_db():
    return psycopg2.connect(
        dbname=PGDATABASE, user=PGUSER, password=PGPASSWORD,
        host=PGHOST, port=PGPORT
    )

# Build SQL query (returns query + whether time alignment is needed)
def get_band_query(device, startdate, enddate, filters, ch_start, ch_end):
    
    filters_list = parse_filters(filters)

    print("Parsed filters_list:", filters_list)

    if ch_start == ch_end:
        filters_list = [{"name": "csum", "params": []}] + filters_list
    if enddate == "":
        NOW_TIMESTAMP = datetime.now().replace(microsecond=0)
        enddate = NOW_TIMESTAMP.isoformat(sep=' ')
        #enddate = "NOW()"

    names = {f["name"] for f in filters_list}
    time_based = True

    if "cps" in names:
        cps_filter = next((f for f in filters_list if f["name"] == "cps"), {"params": []})
        p1 = cps_filter["params"][0] if len(cps_filter["params"]) > 0 else ">0"
        p2 = cps_filter["params"][1] if len(cps_filter["params"]) > 1 else ">0"

        query = f"""
            SELECT date_trunc('second', time) AS time, COUNT(*) AS data
            FROM {device}
            WHERE channels[1] {p1} AND channels[2] {p2}
            AND time > '{startdate}' AND time < '{enddate}'
            GROUP BY 1 ORDER BY 1;
        """
    elif "cpm" in names:
        cpm_filter = next((f for f in filters_list if f["name"] == "cpm"), {"params": []})
        p1 = cpm_filter["params"][0] if len(cpm_filter["params"]) > 0 else ">0"
        p2 = cpm_filter["params"][1] if len(cpm_filter["params"]) > 1 else ">0"

        query = f"""
            SELECT date_trunc('minute', time) AS time, COUNT(*) AS data
            FROM {device}
            WHERE channels[1] {p1} AND channels[2] {p2}
            AND time > '{startdate}' AND time < '{enddate}'
            GROUP BY 1 ORDER BY 1;
        """
    elif "cph" in names:
        cph_filter = next((f for f in filters_list if f["name"] == "cph"), {"params": []})
        p1 = cph_filter["params"][0] if len(cph_filter["params"]) > 0 else ">0"
        p2 = cph_filter["params"][1] if len(cph_filter["params"]) > 1 else ">0"

        query = f"""
            SELECT date_trunc('hour', time) AS time, COUNT(*) AS data
            FROM {device}
            WHERE channels[1] {p1} AND channels[2] {p2}
            AND time > '{startdate}' AND time < '{enddate}'
            GROUP BY 1 ORDER BY 1;
        """
    elif "spectrum" in names:
        spectrum_filter = next((f for f in filters_list if f["name"] == "spectrum"), {"params": []})
        p1 = spectrum_filter["params"][0] if spectrum_filter["params"] else ">0"
        p2 = spectrum_filter["params"][1] if len(spectrum_filter["params"]) > 1 else ">0"
        # Binned spectrum using configurable constants above
        query = f"""
            SELECT ((channels[2]::int / 1) * 1) AS energy,
                   COUNT(*) AS data
            FROM {device}
            WHERE channels[1] {p1}
              AND channels[2] {p2}
              AND time > '{startdate}' AND time < '{enddate}'
            GROUP BY 1
            ORDER BY 1;
        """
        time_based = False
    elif "tsum" in names:
        tsum_filter = next((f for f in filters_list if f["name"] == "tsum"), {"params": []})

        # Older panels use plain "tsum" with separate channel start/end fields.
        a, b = ch_start, ch_end
        if tsum_filter["params"]:
            p1 = int(tsum_filter["params"][0])
            p2 = int(tsum_filter["params"][1])
            if p1 and p2:
                a, b = p1, p2

        query = f"""
            SELECT u.rn AS energy, SUM(u.elem) AS data
            FROM {device} t,
                unnest(t.channels[{a}:{b}]) WITH ORDINALITY AS u(elem, rn)
            WHERE time > '{startdate}' AND time < '{enddate}'
            GROUP BY u.rn ORDER BY u.rn;
        """
        time_based = False
    elif "singlechannel" in names: # new way: singlechannel filter, ch_start and ch_end no longer needed (they're now filter params)
        channel_filter = next((f for f in filters_list if f["name"] == "singlechannel"), {"params": []})
        p1 = channel_filter["params"][0] if channel_filter["params"] else "1"  # Default to channel 1
        aggvalue = channel_filter["params"][1] if len(channel_filter["params"]) > 1 else None  # Optional aggvalue

        if aggvalue:
            # Aggregate over the specified number of seconds
            agg_seconds = int(aggvalue)
            query = f"""
                SELECT
                time_bucket('{agg_seconds} seconds', time) AS time,
                SUM(channels[{p1}]) AS data
                FROM {device}
                WHERE time > '{startdate}' AND time < '{enddate}'
                GROUP BY 1
                ORDER BY 1;                
            """
        else:
            # Original per-second query
            query = f"""
                SELECT time, channels[{p1}] AS data
                FROM {device}
                WHERE time > '{startdate}' AND time < '{enddate}'
                ORDER BY time;
        """        
    elif "csum" in names or "colsum" in names: # fallback (old way of doing things)
        query = f"""
            SELECT time, (SELECT SUM(s) FROM UNNEST(channels[{ch_start}:{ch_end}]) s) AS data
            FROM {device}
            WHERE time > '{startdate}' AND time < '{enddate}'
            ORDER BY time;
        """
    else:
        query = f"""
            SELECT time, channels AS data
            FROM {device}
            WHERE time > '{startdate}' AND time < '{enddate}';
        """

    return query.strip(), time_based

def run_band_query(query_str):
    this_start_time = time.time()
    with generation_control.connection(connect_to_db) as conn:
        print(f"Executing query: {query_str}")
        raw_df = pd.read_sql(query_str, conn)
    duration = time.time() - this_start_time
    print(f"Query took {duration:.2f}s")

    return raw_df, duration

def applyfilters_singleband(df, filters):
    this_start_time = time.time()

    if "time" in df.columns and not df.empty:
        parsed_filters = parse_filters(filters)

        for f in parsed_filters:
            filter_name = f["name"].lower()
            if filter_name in ["cps", "cpm", "cph"]:
                inferred_start = df["time"].iloc[0]
                inferred_end = df["time"].iloc[-1]
                # Set frequency based on filter: '1S' for cps, '1T' for cpm
                if filter_name == "cps":
                    freq = "1S"
                elif filter_name == "cpm":
                    freq = "1T"
                elif filter_name == "cph":
                    freq = "1H"                
                
                print(f"✅ Recognized {filter_name} filter — reindexing at frequency {freq}")
                full_range = pd.date_range(start=inferred_start, end=inferred_end, freq=freq)
                df = df.set_index("time").reindex(full_range, fill_value=0).rename_axis("time").reset_index()
                break  # Process only the first cps or cpm filter

    duration = time.time() - this_start_time
    print(f"Band filter application took {duration:.2f}s")
    return df, duration

def applygranularity_singleband(df, granularity):
    this_start_time = time.time()

    if "time" in df.columns and not df.empty:
        # Ensure the time column is sorted (required for indexing)
        #df = df.sort_values("time")

        granularity_int = int(granularity)
        if granularity_int > 1:
            print(f"Applying granularity={granularity_int} to dataframe of length {len(df)}")
            df = df.iloc[::granularity_int].reset_index(drop=True)

    duration = time.time() - this_start_time
    print(f"Band apply granularity took {duration:.2f}s")
    return df, duration

def merge_allbands(dataframes, headers, time_based):
    merge_start = time.time()

    # Keep your duplicate-name protection & warning
    headers = _make_unique(list(headers))

    if time_based:
        # --- NEW: as-of align each band onto the UNION of all timestamps, with tolerance=5s ---
        tol = pd.Timedelta(seconds=5)

        # 1) Normalise per-band frames
        per_band = []
        for i, df in enumerate(dataframes):
            hdr = headers[i]
            tmp = df[["time", "data"]].copy()
            tmp["time"] = pd.to_datetime(tmp["time"], errors="coerce")
            tmp = tmp.dropna(subset=["time"]) \
                     .drop_duplicates(subset="time") \
                     .sort_values("time")
            per_band.append((hdr, tmp))

        # 2) Build the union time grid
        if per_band:
            union_times = (
                pd.concat([p[1]["time"] for p in per_band], ignore_index=True)
                .dropna()
                .drop_duplicates()
                .sort_values()
                .reset_index(drop=True)
            )
        else:
            union_times = pd.Series([], dtype="datetime64[ns]")

        merged_df = pd.DataFrame({"time": union_times})

        # 3) As-of align each band independently to the union grid
        for hdr, band_df in per_band:
            aligned = pd.merge_asof(
                merged_df[["time"]],            # left: union grid
                band_df,                        # right: this band's timestamps
                on="time",
                direction="nearest",
                tolerance=tol
            )
            # Prevent "nearest" matches from extending a band beyond its actual time span.
            if not band_df.empty:
                band_min = band_df["time"].iloc[0]
                band_max = band_df["time"].iloc[-1]
                outside_span = (merged_df["time"] < band_min) | (merged_df["time"] > band_max)
                aligned.loc[outside_span, "data"] = pd.NA
            merged_df[hdr] = aligned["data"].values

            # Helpful log if a whole band didn't match within tolerance
            if merged_df[hdr].isna().all():
                print(f"ℹ️ Band '{hdr}' has no matches within {tol} "
                      f"(likely fully disjoint or coarser than tolerance).")

        # 4) Pretty-print time after merging
        if not merged_df.empty and pd.api.types.is_datetime64_any_dtype(merged_df["time"]):
            merged_df["time"] = merged_df["time"].dt.strftime("%Y-%m-%d %H:%M:%S.%f")

    else:
        # --- unchanged energy-based path ---
        merged_df = None
        for i, df in enumerate(dataframes):
            header = headers[i]
            df = df.rename(columns={"energy": "energy", "data": header})
            merged_df = df if merged_df is None else pd.merge(merged_df, df, on="energy", how="outer")
        merged_df = merged_df.fillna(0).astype({h: int for h in headers})

    merging_time = time.time() - merge_start
    return merged_df, merging_time

def applygranularity_allbands(merged_df,allbands_info, time_based):
    timings_postprocessing = []
    processed_bands = []

    if time_based:

        for i, band in enumerate(allbands_info):
            gran = int(band.get("granularity", 1) or 1)

            print(f"merged_df_fullres shape: {merged_df.shape}")
            print(f"merged_df_fullres columns: {merged_df.columns.tolist()}")
            print(f"Trying to access columns: 0 and {i + 1}")

            print(merged_df.head())

            band_df = merged_df.iloc[:, [0, i + 1]].copy()
            band_df.columns = ["time", "data"]

            proc_df, postproc_time = applygranularity_singleband(band_df, gran)
            processed_bands.append(proc_df)
            timings_postprocessing.append({
                "postproc": postproc_time
            })

    else:
        # For energy-based bands, granularity is skipped; copy columns directly
        for i, band in enumerate(allbands_info):
            band_df = merged_df.iloc[:, [0, i + 1]].copy()
            band_df.columns = ["energy", "data"]
            processed_bands.append(band_df)

    return processed_bands, timings_postprocessing

def applyfilters_allbands(dataframes, allbands_info):
    timings_postprocessing = []
    processed_bands = []

    for i, (df, band) in enumerate(zip(dataframes, allbands_info)):
        filters = band.get("filters", "")
        proc_df, postproc_time = applyfilters_singleband(df, filters)

        processed_bands.append(proc_df)
        timings_postprocessing.append({"filter": postproc_time})

    return processed_bands, timings_postprocessing


def run_queries_allbands(queries):
    raw_dataframes = []
    timings_queries = []

    for query in queries:
        raw_df, sql_time = run_band_query(query)
        raw_dataframes.append(raw_df)
        timings_queries.append({"sql": sql_time})

    return raw_dataframes, timings_queries


def _make_unique(names):
    seen = {}
    out = []
    for n in names:
        if n in seen:
            seen[n] += 1
            new_name = f"{n} ({seen[n]})"
            print(f"⚠️  Duplicate header '{n}' detected — renaming to '{new_name}'")
            out.append(new_name)
        else:
            seen[n] = 1
            out.append(n)
    return out

def merge_energy_spectra(snapshot_df: pd.DataFrame, db_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merges two energy-based DataFrames by:
    1. Grouping by energy to sum duplicate rows.
    2. Performing an outer merge on 'energy'.
    3. Combining matching columns (_x, _y) into one.

    Args:
        snapshot_df (pd.DataFrame): The snapshot DataFrame.
        db_df (pd.DataFrame): The database DataFrame.

    Returns:
        pd.DataFrame: Merged DataFrame with combined spectra.
    """

    # Step 1: Group by energy and sum duplicate rows
    snapshot_grouped = snapshot_df.groupby("energy", as_index=False).sum()
    db_grouped       = db_df.groupby("energy", as_index=False).sum()

    # Step 2: Outer merge on energy
    merged = pd.merge(snapshot_grouped, db_grouped, on="energy", how="outer")

    # Step 3: Combine _x and _y columns into single columns
    for col in merged.columns:
        if col.endswith("_x"):
            base = col[:-2]
            col_x = col
            col_y = base + "_y"

            if col_y in merged.columns:
                merged[base] = merged[col_x].fillna(0) + merged[col_y].fillna(0)
                merged.drop([col_x, col_y], axis=1, inplace=True)

    # Optional: sort by energy
    merged = merged.sort_values(by="energy").reset_index(drop=True)

    return merged


###################################################################

# ---- main execution starts ----
with open(json_filepath, "r") as f:
    thispanel = json.load(f)

graph_index = int(graph_no) - 1
thisgraph = thispanel["graphs"][graph_index]
allbands_info = thisgraph["bands"]

queries_to_be_used = []
time_based_flags = []

global_start_time = time.time()

# Build queries for all bands
for band in allbands_info:
    band_starttime = band.get("start") or thisgraph.get("start") or thispanel["start"] # deal with the actual start and end time here
    band_endtime = band.get("end") or thisgraph.get("end") or thispanel.get("end", "")
    band_startchannel = band.get("channelsstart", 1)
    band_endchannel = band.get("channelsend", 2)

    band_query, time_based = get_band_query(band["device"], band_starttime, band_endtime, band.get("filters", ""), band_startchannel, band_endchannel)

    queries_to_be_used.append(band_query)
    time_based_flags.append(time_based) 

print(queries_to_be_used)

# Check for snapshot match (before executing anything)
normalized_queries = [normalize_sql(q) for q in queries_to_be_used]
#cached_processed_csv, cached_fullres_csv, snapshot_name_found = find_snapshot_csv(subfolder, graph_no, normalized_queries)
cached_processed_csv, cached_fullres_csv, snapshot_name_found, snapshot_premature_endtime = find_snapshot_csv_partial(
    subfolder, graph_no, normalized_queries
)

timings_queries = []
timings_filter = []
timings_granularity = []
normalized_queries_adjusted = None
merging_time_processed = None

if snapshot_name_found and not snapshot_premature_endtime:
    print(f"✅ Using snapshot: {snapshot_name_found}")
    print(f"cached_processed_csv: {cached_processed_csv}")

    # Load metadata from snapshot folder (not meta_filepath!)
    snapshot_meta_path = csvfolder / subfolder / panel_name / snapshot_name_found / f"{panel_name}-{graph_no}.meta.json"
    with open(snapshot_meta_path, "r") as f:
        snapshot_meta_data = json.load(f)

    cached_grans = [str(g) for g in snapshot_meta_data.get("granularities", [])]
    current_grans = [str(band.get("granularity", 1)) for band in allbands_info]
    granularities_match = cached_grans == current_grans

    if granularities_match:
        print("✅ Granularities match cached version — copying snapshot files to base folder.")

        # Copy all three snapshot files to main location
        with generation_control.output(csv_filepath) as output_path:
            shutil.copy(cached_processed_csv, output_path)
        with generation_control.output(fullres_filepath) as output_path:
            shutil.copy(cached_fullres_csv, output_path)
        with generation_control.output(meta_filepath) as output_path:
            shutil.copy(snapshot_meta_path, output_path)

        row_count_fullres = snapshot_meta_data.get("row_count_fullres")
        row_count_processed = snapshot_meta_data.get("row_count_processed")
        csv_size_mb = snapshot_meta_data.get("csv_size_mb")
        fullres_csv_size_mb = snapshot_meta_data.get("fullres_csv_size_mb")

        print(f"✅ Files copied to: {csv_filepath}")
        print(f"✅ Skipping data loading and processing — early exit.")

    else:
        print("⚠️ Granularities differ — reapplying postprocessing.")

        # Load full-resolution data (now needed)
        loading_start = time.time()
        merged_df_fullres = pd.read_csv(cached_fullres_csv)
        loading_time_fullres = time.time() - loading_start
        print(f"✅ Loaded full-resolution CSV to: {cached_fullres_csv}")

        # Re-apply granularity
        processed_bands, timings_granularity = applygranularity_allbands(merged_df_fullres, allbands_info, any(time_based_flags))

        merged_df_processed, merging_time_processed = merge_allbands(processed_bands, [band["header"] for band in allbands_info], any(time_based_flags))

        with generation_control.output(csv_filepath) as output_path:
            merged_df_processed.to_csv(output_path, index=False, na_rep="", quoting=csv.QUOTE_NONE, escapechar="\\")
        print(f"✅ Saved postprocessed CSV to: {csv_filepath}")

elif snapshot_name_found and snapshot_premature_endtime:
    print(f"✅ Using snapshot: {snapshot_name_found}")
    print(f"Partial snapshot ends at {snapshot_premature_endtime}")

    # Load snapshot
    loading_start = time.time()
    snapshot_df = pd.read_csv(cached_fullres_csv)
    loading_time_fullres = time.time() - loading_start
    print(f"✅ Loaded full-resolution CSV to: {cached_fullres_csv}")

    # Warn if the snapshot range extends beyond the requested end time.
    if any(time_based_flags):
        snapshot_end_ts = pd.to_datetime(snapshot_premature_endtime, errors="coerce")
        if not pd.isna(snapshot_end_ts) and getattr(snapshot_end_ts, "tzinfo", None) is not None:
            snapshot_end_ts = snapshot_end_ts.tz_convert(None)

        requested_end_candidates: list[pd.Timestamp] = []
        for band in allbands_info:
            band_end = band.get("end") or thisgraph.get("end") or thispanel.get("end", "")
            band_end_ts = pd.to_datetime(band_end, errors="coerce")
            if not pd.isna(band_end_ts) and getattr(band_end_ts, "tzinfo", None) is not None:
                band_end_ts = band_end_ts.tz_convert(None)
            if not pd.isna(band_end_ts):
                requested_end_candidates.append(band_end_ts)

        requested_end_ts = max(requested_end_candidates) if requested_end_candidates else pd.NaT

        if not pd.isna(snapshot_end_ts) and not pd.isna(requested_end_ts) and snapshot_end_ts > requested_end_ts:
            print(
                "⚠️ Snapshot end time is after the requested end time "
                f"({snapshot_end_ts} > {requested_end_ts}). "
                "No additional DB data will be fetched because the adjusted tail query window is empty "
                "(get_adjusted_time_range() clamps the adjusted start to the requested end to avoid an inverted SQL range). "
                "Graph output may include data beyond the requested range; "
                "consider creating a new snapshot for this time window or updating the panel end time."
            )

    # --- Adjust query start times ---
    queries_to_be_used_adjusted = []
    normalized_queries_adjusted = []

    for i, band in enumerate(allbands_info):
        band_start = band.get("start") or thisgraph.get("start") or thispanel["start"]
        band_end   = band.get("end")   or thisgraph.get("end")   or thispanel.get("end", "")
        ch_start   = band.get("channelsstart", 1)
        ch_end     = band.get("channelsend", 2)

        adj_start, adj_end = (
            get_adjusted_time_range(band_start, band_end, snapshot_premature_endtime)
            if any(time_based_flags) else (band_start, band_end)
        )

        band_query, _ = get_band_query(
            device=band["device"],
            startdate=adj_start,
            enddate=adj_end,
            filters=band.get("filters", ""),
            ch_start=ch_start,
            ch_end=ch_end,
        )

        queries_to_be_used_adjusted.append(band_query)
        normalized_queries_adjusted.append(normalize_sql(band_query))

    # --- Run adjusted queries ---
    raw_banddata, timings_queries = run_queries_allbands(queries_to_be_used_adjusted)

    # --- Apply filters (e.g. cps) ---
    filtered_banddata, timings_filter = applyfilters_allbands(raw_banddata, allbands_info)

    # --- Merge newly queried filtered data ---
    merged_db_df_fullres, merging_time_fullres = merge_allbands(filtered_banddata, [band["header"] for band in allbands_info], any(time_based_flags))

    # --- Combine snapshot + DB data ---
    if all(time_based_flags):
        print(f"✅ Time-based: safe to concatenate")
        merged_df_fullres = pd.concat([snapshot_df, merged_db_df_fullres]) 
    else:
        print(f"❌ Energy-based: merge on energy and sum matching columns")
        merged_df_fullres = merge_energy_spectra(snapshot_df, merged_db_df_fullres)

    with generation_control.output(fullres_filepath) as output_path:
        merged_df_fullres.to_csv(output_path, index=False, na_rep="", quoting=csv.QUOTE_NONE, escapechar="\\")
    print(f"✅ Combined fullres saved to {fullres_filepath}")

    processed_bands, timings_granularity = applygranularity_allbands(merged_df_fullres, allbands_info, any(time_based_flags))

    merged_df_processed, merging_time_processed = merge_allbands(processed_bands, [band["header"] for band in allbands_info], any(time_based_flags))

    with generation_control.output(csv_filepath) as output_path:
        merged_df_processed.to_csv(output_path, index=False, na_rep="", quoting=csv.QUOTE_NONE, escapechar="\\")
    print(f"✅ Saved postprocessed CSV to: {csv_filepath}")
    print(merged_df_processed.head())    

else:
    # No snapshot match – run queries
    raw_banddata, timings_queries = run_queries_allbands(queries_to_be_used)

    filtered_banddata, timings_filter = applyfilters_allbands(raw_banddata, allbands_info)

    merged_df_fullres, merging_time_fullres = merge_allbands(filtered_banddata, [band["header"] for band in allbands_info], any(time_based_flags))

    with generation_control.output(fullres_filepath) as output_path:
        merged_df_fullres.to_csv(output_path, index=False, na_rep="", quoting=csv.QUOTE_NONE, escapechar="\\")
    print(f"✅ Saved full-resolution CSV to: {fullres_filepath}")
    print(merged_df_fullres.head())

    processed_bands, timings_granularity = applygranularity_allbands(merged_df_fullres, allbands_info, any(time_based_flags))

    merged_df_processed, merging_time_processed = merge_allbands(processed_bands, [band["header"] for band in allbands_info], any(time_based_flags))

    with generation_control.output(csv_filepath) as output_path:
        merged_df_processed.to_csv(output_path, index=False, na_rep="", quoting=csv.QUOTE_NONE, escapechar="\\")
    print(f"✅ Saved postprocessed CSV to: {csv_filepath}")
    print(merged_df_processed.head())


if snapshot_name_found:
    snapshot_type = "partial" if snapshot_premature_endtime else "full"
    snapshot_end_time_used = snapshot_premature_endtime
else:
    snapshot_type = None
    snapshot_end_time_used = None

# Save metadata
metadata = {
    "snapshot_used": snapshot_name_found,
    "snapshot_type": snapshot_type,  # NEW
    "snapshot_end_time_used": snapshot_end_time_used,  # NEW    
    "total_time": round(time.time() - global_start_time, 3),
    "queries": normalized_queries,
    "adjusted_queries": normalized_queries_adjusted if normalized_queries_adjusted is not None else None,
    "granularities": [band.get("granularity", 1) for band in allbands_info],  # NEW LINE
    "timings_queries_per_band": timings_queries,
    "merging_time_fullres": round(merging_time_fullres, 3) if 'merging_time_fullres' in locals() else None,
    "loading_time_fullres": round(loading_time_fullres, 3) if 'loading_time_fullres' in locals() else None,
    "timings_filterpostprocessing_per_band": timings_filter,
    "timings_granularitypostprocessing_per_band": timings_granularity,  
    "merging_time_processed": round(merging_time_processed, 3) if isinstance(merging_time_processed, (int, float)) else None,
    "row_count_fullres": row_count_fullres if 'row_count_fullres' in locals() else len(merged_df_fullres),
    "row_count_processed": row_count_processed if 'row_count_processed' in locals() else len(merged_df_processed),
    "csv_size_mb": csv_size_mb if 'csv_size_mb' in locals() else round(os.path.getsize(csv_filepath) / 1024**2, 3),
    "fullres_csv_size_mb": fullres_csv_size_mb if 'fullres_csv_size_mb' in locals() else round(os.path.getsize(fullres_filepath) / 1024**2, 3),
}

print(metadata)

with generation_control.output(meta_filepath) as output_path, open(output_path, "w") as f:
    json.dump(metadata, f, indent=2)

print(f"✅ Written metadata to: {meta_filepath}")
print(f"✅ Saved CSV to: {csv_filepath}")

from urllib.parse import quote, urljoin

# PHP supplies the public viewer URL; direct command-line runs keep a relative link.
relative_meta_path = meta_filepath.relative_to(csvfolder)
meta_url = f"csv-files/{quote(relative_meta_path.as_posix())}"
if args.base_url:
    meta_url = urljoin(args.base_url.rstrip('/') + '/', meta_url)
print(f"🔗 Metadata URL: {meta_url}")

# Data records, tables and panel filters

The viewer reads time-series records from a PostgreSQL database with the
TimescaleDB extension. Each row represents something recorded at a moment in
time: a measurement, several simultaneous measurements, a detector pulse, or an
accumulated spectrum. Rows can arrive at regular or irregular intervals.

A panel describes which tables to read and how to turn their records into
graphs. Its filters can select array elements, count events in time intervals,
or combine records into a spectrum. These operations query the data without
changing the stored records.

The examples below illustrate the shared record format. The optional
[example seed command](../deploy/README.md#3-create-the-example-data-and-panel)
creates pressure and pulse tables using this format. For everyday panel controls,
see [Using the viewer](using-viewer.md).

## Table structure

The standard measurement-table structure, also shown on the viewer's homepage,
is:

```sql
CREATE TABLE example_pressure (
    time TIMESTAMP NOT NULL,
    channels FLOAT[] NOT NULL,
    id BIGSERIAL
);

SELECT create_hypertable('example_pressure', 'time');
```

Replace `example_pressure` with your table name. Run these commands in the
application database with an account allowed to create tables. TimescaleDB must
already be enabled there. The Ubuntu installer creates the database and enables
the extension; measurement tables are created separately.

| Column | Meaning |
| --- | --- |
| `time` | Timestamp of the measurement or event. Used for time selection and aggregation. |
| `channels` | An array of numerical values belonging to that record. |
| `id` | An automatically generated record number. |

Different records may share a timestamp. For pulse data, the table can also
include a `ps BIGINT` column. The CAEN ingestion format uses it for the
picosecond remainder within the second, alongside the timestamp's microsecond
precision. The graph filters described here read `time` and `channels`.

`TIMESTAMP` in this schema has no timezone attached. Record timestamps and panel
time windows must use a consistent timezone. The viewer's `display_timezone`
setting controls the homepage's table timestamps; it does not convert stored
records or graph-query time windows.

Use the database owner or an ingestion account to insert measurements. The
viewer itself uses a read-only database account. See
[database configuration](configuration.md#database-configuration) for connection
settings and account setup.

## What goes in `channels`?

`channels` is a numerical array. Its entries have no built-in names or units;
their meaning is defined by the data producer. Keep the order, meaning and units
consistent within each table, and describe them in panel notes and band labels.

These are different possible record layouts:

| Kind of record | Example `channels` value | Interpretation |
| --- | --- | --- |
| One pressure reading | `[1.03]` | Pressure in bar. |
| Several simultaneous measurements | `[1.03, 22.4, 0.5]` | Pressure, temperature and voltage, in that order. |
| One detector pulse | `[0.28, 850]` | Pulse-shape parameter (PSP), followed by energy. |
| One accumulated spectrum | `[1, 0, 4, 2]` | Counts in four successive energy bins for that acquisition interval. |

The bracket notation above illustrates array contents. For a PostgreSQL insert,
use an array expression such as:

```sql
INSERT INTO example_pressure (time, channels)
VALUES ('2026-01-01 12:00:00', ARRAY[1.03]);
```

Array positions are **numbered from 1**: `channels[1]` is the first value,
`channels[2]` is the second, and so on. A table can contain more than two values
per record; for example, `singlechannel(20)` selects its twentieth value.

An array position need not correspond to a physical detector channel. For
example, a table can represent one detector while its two array entries hold
that detector's PSP and energy. A stored spectrum uses the same array type, but
each position represents an energy bin instead.

The generic array-selection filters work with either layout. The event-count
and pulse-spectrum filters specifically interpret the first two entries as
**PSP and energy**.

## Applying a filter in a panel

A panel contains graphs, and each graph contains one or more **bands**. A band
is a plotted series with its own label, table, filter and optional time window.

In an editable installation:

1. Open the panel and click **show editor**.
2. Find the graph and its **Bands** entries.
3. Set **Device** to the database table name, **Label** to the series label, and
   **Filters** to one of the expressions described below.
4. Panel edits save automatically. Use **refresh** to regenerate the graphs
   with the saved settings.

For example, this band selects the first value from the pressure table:

```json
{
    "header": "Pressure (bar)",
    "device": "example_pressure",
    "filters": "singlechannel(1)"
}
```

This object belongs inside a graph's `bands` array in the panel JSON. The
`device` field names a table in the installation's configured database; it does
not select another database.

Use lowercase filter names and one query filter per band. To compare different
filters, add separate bands. Keep time-based bands and spectrum/bin-based bands
in separate graphs so that each graph has one kind of horizontal axis.

| Expression | Result | Horizontal axis |
| --- | --- | --- |
| `singlechannel(1)` | The first value from each record. | Time |
| `singlechannel(1,60)` | Sum of the first value in each 60-second bucket. | Time |
| `cps(>0.22,>0)` | Matching pulse count per second. | Time |
| `cpm(>0.22,>0)` | Matching pulse count per minute. | Time |
| `cph(>0.22,>0)` | Matching pulse count per hour. | Time |
| `spectrum(>0.22,>0)` | Histogram of matching pulse energies across the chosen time window. | Energy |
| `tsum(1,4)` | Sum of stored spectrum arrays, using positions 1–4. | Bin position |
| `csum` or `colsum` | Sum of a chosen range of array values within each record. | Time |

## Selecting a value: `singlechannel`

```text
singlechannel(position)
singlechannel(position,bucket_seconds)
```

Use a positive integer array position. `singlechannel(1)` selects
`channels[1]`; `singlechannel(2)` selects `channels[2]`. With no position,
`singlechannel()` defaults to the first value.

For a table holding `[pressure, temperature, voltage]`, three bands using
`singlechannel(1)`, `singlechannel(2)` and `singlechannel(3)` select the three
measurements separately. Choose graph groupings and labels appropriate to their
different units.

The optional second argument is a positive integer number of seconds. For
example, `singlechannel(1,60)` uses TimescaleDB's `time_bucket` to group records
into 60-second intervals and **sum** `channels[1]` within each interval.
It does not calculate an average. For a direct pressure trace, use
`singlechannel(1)`.

## Counting events: `cps`, `cpm` and `cph`

These filters count **rows representing individual events**, with conditions
on the first two array entries:

```text
cps(psp_condition,energy_condition)
cpm(psp_condition,energy_condition)
cph(psp_condition,energy_condition)
```

The conditions are comparisons against the stored numerical values. Write the
comparison operator and value, without quoting them:

```text
cpm(>0.22,>=500)
```

This selects records where **both** conditions hold:

```sql
channels[1] > 0.22 AND channels[2] >= 500
```

It then counts the selected rows in each calendar minute. `cps` groups by
second, and `cph` groups by hour. These are counts in those intervals, with no
conversion to another rate unit. A partially selected minute or hour contains
only the events inside the requested time window; its count is not extrapolated
to a full interval.

Other comparisons include `<`, `<=`, `=`, `>=`, `>` and `<>`. For example,
`cps(<=0.22,>0)` selects PSP values at or below the threshold and positive
energies. Choose thresholds appropriate to the data; `0.22` is only an example.
The second comparison uses the energy units actually stored in the array.

An omitted condition defaults to `>0`. For example, `cps()` means
`cps(>0,>0)`, and `cps(>0.22)` means `cps(>0.22,>0)`.
These defaults exclude zero, negative and SQL `NULL` values in the relevant
positions. A missing second array element also cannot pass its comparison.

The generator fills empty intervals with zero between the first and last
matching time buckets. It does not pad empty intervals before or after that
span to the full panel time range.

For a row that holds an accumulated spectrum, these filters would count
spectrum records. To obtain counts from the contents of such records, use the
array-summing filters below.

## Building a pulse-energy spectrum: `spectrum`

```text
spectrum(psp_condition,energy_condition)
```

This filter uses the same PSP/energy conditions and defaults as the event-count
filters, but groups the matching pulses by energy instead of time:

1. Select pulse rows in the requested time window.
2. Apply the PSP comparison to `channels[1]` and the energy comparison to
   `channels[2]`.
3. Convert the energy value to an integer and count pulses at each resulting
   energy value.

The graph shows energy horizontally and pulse count vertically. The filter
does not calibrate energy into physical units; it uses the values stored in
`channels[2]`.

For example, two bands using `spectrum(>=0,>0)` and `spectrum(>0.22,>0)` compare
pulses with nonnegative PSP against a subset above the illustrative PSP
threshold. Both require positive energy.

Each spectrum combines pulses over its selected time window. To compare earlier
and later spectra, add two spectrum bands with different start/end times.
Changing those times and refreshing regenerates the spectra. Zooming a time
graph does not automatically change a spectrum's query window. This viewer
displays spectrum curves; it does not display a time-versus-energy heatmap.

## Working with stored spectrum arrays

A stored spectrum record contains counts per bin, rather than one pulse's
attributes. For example:

| Time | `channels` |
| --- | --- |
| 12:00:00 | `[1, 0, 4, 2]` |
| 12:01:00 | `[2, 3, 1, 0]` |

### Combine spectra across time: `tsum`

```text
tsum(first_position,last_position)
```

The range is inclusive. `tsum(1,4)` adds the corresponding array positions
across all records in the time window. For the two rows above, it produces:

```text
bin 1: 3
bin 2: 3
bin 3: 5
bin 4: 2
```

The output horizontal axis is labelled `energy`, but its values are positions
within the selected array slice, numbered from 1. For example, `tsum(3,4)`
labels the selected original positions 3 and 4 as 1 and 2. It does not apply an
energy calibration.

Older panels may use plain `tsum` with `channelsstart` and `channelsend` fields
in the band JSON. That form uses those fields as the inclusive range. Use both
explicit arguments, such as `tsum(1,4)`, when specifying a range in the filter.

### Follow one bin over time: `singlechannel`

`singlechannel(3)` selects the third bin's count in each record. For the example
above, the values are 4 at 12:00 and 1 at 12:01. This produces a time graph of
that bin's contents.

### Sum bins within each record: `csum` or `colsum`

These equivalent filters use the band's `channelsstart` and `channelsend`
fields as an inclusive range:

```json
{
    "header": "Total counts per spectrum",
    "device": "example_spectra",
    "channelsstart": "1",
    "channelsend": "4",
    "filters": "csum"
}
```

For the example rows, this produces 7 at 12:00 and 6 at 12:01. The sum is taken
within each row, preserving time as the horizontal axis. `tsum`, by comparison,
adds rows together while preserving separate bins. These results are counts per
stored spectrum; their units depend on the acquisition interval represented by
each record.

## Time windows and granularity

For each band, start and end times are taken from the first nonempty setting at
these levels:

1. The band.
2. Its graph.
3. The panel.

The panel must provide a start time. If no end time is set at any level, the
generator uses its current time. Queries use `time > start` and `time < end`,
so records exactly on either boundary are excluded.

The editor's **Gran.** field corresponds to `granularity` in a band. For time
graphs, a value greater than 1 keeps every Nth point after the bands have been
aligned for plotting. For example, `10` keeps every tenth point. A blank value
or `1` keeps all points. This reduces displayed points; it does not average or
sum them, and it does not change the filter's query interval. Granularity is
not applied to spectrum/bin-based graphs.

The graph's **query** link in an editable installation shows the generated SQL
and diagnostics, allowing you to inspect how the table, filter and time window
were interpreted.

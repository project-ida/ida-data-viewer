# IDA Data Viewer

IDA Data Viewer is a web interface for exploring time-series measurements stored
in PostgreSQL with TimescaleDB. It grew out of experimental work combining
neutron and gamma measurements from scintillation detectors with supporting
diagnostics such as temperature, pressure, current and voltage.

Measurements are displayed in configurable **panels**, each containing an
experiment description, notes and multiple graphs. Filters select individual
measurements, aggregate events into count rates, or build energy spectra. The
viewer supports both regularly sampled measurements and events recorded at
irregular intervals, using a shared timestamp-and-values table format.

## What it provides

- A homepage listing database tables, their latest timestamps and experiment panels.
- Interactive graphs with zoom, linear/logarithmic scales and optional synchronised
  time-axis zoom across graphs.
- A panel editor for choosing tables, time windows, plotted series and filters.
- OneFileCMS for copying, renaming and organising panel files, and Adminer for
  database administration.
- CSV downloads and snapshots of generated graph data.
- Optional links to a separately installed waveform viewer for individual detector pulses.

Graphs load when a panel is opened. Refresh the page to regenerate graphs as
new measurements arrive or after changing the panel settings.

## Getting started

1. Follow the [Ubuntu installation guide](deploy/README.md) on a fresh, dedicated
   Ubuntu 24.04 amd64 server. The installer configures Apache/PHP, Python,
   PostgreSQL 16, TimescaleDB, HTTPS and password-protected website access,
   and creates synthetic example data and an editable panel.
2. Open `https://YOUR_HOSTNAME/` and sign in with the website account you chose
   during setup (include your URL path if configured, for example `/data/`).
   Open `example-panel.json` under **Panels**.
3. Follow [Using the viewer](docs/using-viewer.md) to explore the graphs and edit
   the panel. Use **Create new panel** on the homepage to start your own panel,
   or copy an existing one through **manage panels**.

The example is included automatically on first installation. Later installer
runs preserve existing data and panel edits, and do not recreate examples you
have deleted.

## Your first panel

The example contains six days of synthetic pressure and radiation-pulse data:

- Pressure sampled once per second.
- Gamma-candidate counts per minute.
- Neutron-candidate counts per minute, with three five-minute bursts.
- Gamma and neutron energy spectra over the same time window.

Click **show editor** to see how its graphs are configured. Each plotted series
is called a **band** and specifies a database table and filter. The example
demonstrates selecting one measurement, counting events and applying PSP/energy
thresholds. Its historical timestamps can make its table rows appear red on the
homepage; that is expected for a fixed example dataset.

## Using your own measurements

Your acquisition or import process inserts measurements into the database.
The viewer reads those records and turns them into graphs; the example seed
script supplies synthetic measurements for trying out that workflow.

Each measurement table has a timestamp and a numerical `channels` array. A row
can hold one pressure reading, several simultaneous measurements, an individual
detector pulse, or an accumulated spectrum. The
[data and filters guide](docs/data-and-filters.md) explains the table structure,
array meanings, SQL examples and available filters.

Each installation uses one database. Panel folders organise experiments and
can be nested independently of the database tables. The installer creates a
read-only database account for graph queries; website access and panel editing
are configured separately. See [Configuration](docs/configuration.md) for
connection settings, integrations and options for publishing prepared panels.

## Documentation

| Guide | Contents |
| --- | --- |
| [Ubuntu installation](deploy/README.md) | Install the complete stack with example data, sign in and maintain the server. |
| [Using the viewer](docs/using-viewer.md) | Homepage, table status, panels, editing, downloads and snapshots. |
| [Data records and filters](docs/data-and-filters.md) | Measurement tables, `channels` arrays, count rates and spectra. |
| [Configuration](docs/configuration.md) | Settings files, manual setup, waveform links and private/public deployments. |

Component references: [OneFileCMS](onefilecms/README.md) and
[Adminer](postgres/README.md). See [LICENSE.md](LICENSE.md) for licensing.

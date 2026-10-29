# Using the viewer

This guide describes an editable, password-protected installation such as the
one created by the [Ubuntu installer](../deploy/README.md). For public viewing
of prepared panels, see [deployment and access control](configuration.md#deployment-and-access-control).

## Open the homepage

Open the URL reported by the installer (`https://YOUR_HOSTNAME/` by default,
or, for example, `https://YOUR_HOSTNAME/data/` with a configured URL path) and
enter the website username and password chosen during installation.
The homepage has two main sections: **Tables** and **Panels**.
An installation can also have [custom links](configuration.md#custom-homepage-links)
to other tools below them.

### Tables and their status

The **Tables** section lists tables in the database's `public` schema. For tables
the viewer can read and that have a suitable `time` column, it shows the latest
recorded timestamp. Tables start in the active list unless marked otherwise.
Table status loads separately: the panels, **manage tables** link and other
homepage controls are available while it loads. If the request fails, a message
appears in the Tables section.

| Appearance or state | Meaning |
| --- | --- |
| Green row | The latest recorded timestamp is within the last 90 minutes. |
| Red row | The latest recorded timestamp is older than 90 minutes. |
| Uncoloured row with `-` | No latest timestamp is available. |
| Inactive table | Listed separately without freshness colouring; shows its last known timestamp when available. |
| Archived table | Hidden from both the active and inactive lists. |

The colours reflect recorded timestamps. A red row may be expected for a
completed experiment or the historical example dataset. Refresh the homepage,
or use **Force refresh all timestamps**, to request updated timestamps. The
homepage's timezone is set through [`display_timezone`](configuration.md#homepage-timezone).

Use **Make inactive** for a table whose incoming data no longer needs monitoring.
It moves into **Inactive tables**, where **Make active** restores monitoring and
**Archive** hides it from the lists. These choices affect the homepage only:
they do not stop an acquisition process, delete database records or prevent a
panel from querying the table.

These choices are saved in `inactive_tables.txt` and `archived_tables.txt` in
the repository folder. The files are local to the installation and ignored by
Git. See [table visibility files](configuration.md#table-visibility-files) for
their format and how to restore an archived table.

### Manage tables

**manage tables** opens Adminer. The database server, port and database name are
already filled in; sign in with your database administrator account. This is a
separate login from the website account.

Adminer provides SQL commands and table administration. The homepage includes
example SQL, and the [data guide](data-and-filters.md#table-structure) explains
how to create measurement tables. Your acquisition or import process supplies
the records that the viewer will plot.

### Panels and folders

The **Panels** section lists JSON panel files and their start/end dates. Expand
folders to find panels organised by experiment, project or archive. Click a
panel to open it in a new tab.

Panel folders do not select databases. A panel can combine tables from the
installation's single configured database, regardless of its folder location.
Use **Create new panel** to start an empty panel, or open the
[installed example](../deploy/README.md#synthetic-data-reference) to explore
synthetic measurements and configured graphs.

## Explore the example panel

The Ubuntu installer creates `example-panel.json` automatically. Open it from
the homepage. It contains:

| Graph | What it demonstrates |
| --- | --- |
| Pressure over time | `singlechannel(1)` selects the pressure value from each record. **Gran. 60** initially displays every 60th reading. |
| Gamma counts per minute | `cpm(<0.22,>0)` counts pulses with PSP below 0.22. |
| Neutron counts per minute | `cpm(>0.22,>500)` counts pulses with PSP above 0.22 and energy above 500. |
| Pulse energy spectra | Two `spectrum(...)` bands compare the same gamma and neutron selections across the full time window. |

The installed data span six days, starting at `2026-01-01 00:00:00`.
Three five-minute neutron bursts start 24, 60 and 108
hours into the data. The measurements and thresholds are illustrative.

Move the pointer over a graph to inspect values in its legend. Drag across a
region to zoom and double-click the graph to reset its zoom. Check
**synchronize time axis zoom** to apply zoom changes across time-based graphs;
energy spectra keep their own horizontal axes. Legend checkboxes hide or show
individual bands, and **log scale** / **lin scale** change the vertical scale.

Each graph reserves space while its data load and displays a loading indicator.
Live generation is limited to two graphs at a time across users by default;
other graphs keep their loading indicators until a slot is available. Adjust
[`max_graph_generators`](configuration.md#graph-generation-limit) in `config.ini`
to change this limit.
While waiting, the browser retries short requests roughly every two seconds;
the waiting graph does not hold a web-server worker or connection open.
If generation fails, an error appears in that space. On an editable installation,
click the graph's **query** link for the generated SQL and diagnostic output.
Opening that link runs the generator again for that graph.

### Cancel graph generation

Use this button when someone unintentionally starts a demanding graph query,
such as opening a panel with a very long time range that pulls far more database
records than expected. It lets you stop the viewer's database retrieval and
processing work, including waiting graph requests, before adjusting the panel
and trying again.

On the homepage, the **Cancel graph loading** section follows the panel list
and its management controls, before any custom links. **Cancel all graph
generation** asks for confirmation and then cancels running and waiting graph
requests across all users and tabs of this installation. The button briefly
shows **Cancellation requested**, then returns to its usual label.
Affected graphs replace their loading indicator with **Graph
generation cancelled. Reload the panel to try again.** Graphs already displayed
stay visible. Reload a panel to start fresh requests after cancellation.

The control is available on editable installations. It also stops generation
started through a **query** link; prepared CSV reads in read-only mode and
direct command-line Python runs are separate.

## Create a panel

1. On the homepage, click **Create new panel** under **Panels**.
2. Enter a name using letters, numbers, spaces, hyphens or underscores, starting
   with a letter or number. New files have no extension; an entered `.json`
   suffix is removed.
3. The new panel opens in a separate tab or window with its editor visible.
   The homepage stays open and its panel list updates automatically. Add a
   graph, then use the **item** add button under
   **Bands** to add a plotted series. Choose its table
   and filter as described below.

New panels are created at the top level of `json-files/`, with the supplied
name as their title, a start time of today at midnight in the configured
timezone, an open end time, empty notes and no graphs. A name such as `chamber5`
creates `json-files/chamber5` and opens `load-panel.php?filename=chamber5`.
Existing names are rejected in both extensionless and `.json` forms. Creating an empty
panel does not query or change the database. Panel creation is available only
when editing is enabled, behind the installation's website login.

Use **manage panels** to move the file into a folder afterwards, or to copy an
existing panel when you want to reuse its graph configuration.

If your browser blocks the new tab, open the new panel from the updated
panel list.

## Edit a panel

A **panel** contains a title, notes, a time window and a list of **graphs**.
Each graph contains one or more **bands**: the plotted series, each with its
own label, table and filter.

1. Open a panel and click **show editor**. The editor opens from the right.
2. Change the panel's title, notes or start/end times as needed.
3. In a graph's **Bands**, set **Device** to a database table name, **Label** to
   the series label and **Filters** to the desired expression.
4. Edits save automatically to that panel's JSON file. Text edits are queued for
   saving after half a second without typing, or when you leave the field.
   Saves run one at a time; edits made during a save are combined into the next
   save. If a save fails, a message appears above the graphs; keep the page open
   to retain your unsaved edits.
5. Use **refresh** after your changes have saved to regenerate the graphs.
   **hide editor** closes the editor without changing the saved panel.

The editor also supports adding or removing graphs and bands. Put series with
compatible units and horizontal axes together in one graph. For example, count
rates use a time axis, while a spectrum uses an energy axis.

Graph data are requested when a panel opens or the page is refreshed; the page
does not periodically poll for new records. Leave the end time empty to query
up to the generation time on each refresh. Set it explicitly for a completed
experiment. Graph and band times can override the panel window; see
[time windows and granularity](data-and-filters.md#time-windows-and-granularity).

Notes are plain text and can contain clickable web addresses. Use notes and
band labels to explain measurement units, array entries and experimental context.
The [filter reference](data-and-filters.md#applying-a-filter-in-a-panel) gives
expressions and examples for different kinds of records.

## Copy and organise panels

From the homepage, open **manage panels** and sign in to OneFileCMS using the
separate CMS account. It manages files inside `json-files/`.

To reuse an existing graph configuration, copy `example-panel.json` or another
suitable panel using the CMS's **Copy** action. Give the copy a different filename,
such as `my-experiment.json`. Return to the homepage, refresh the panel list, open
the copy and edit its title, dates, tables and filters.

The CMS can also rename, move and delete panel files, and create folders.
Panels can use `.json` filenames or filenames without an extension. Nested
folders are supported. Deleting a panel file leaves its database measurements
intact. Copying a panel copies its configuration; its graph data are generated
when the copy is opened.

CMS account settings and diagnostics are covered in the
[OneFileCMS reference](../onefilecms/README.md).

## Download graph data

Each graph has two CSV download links:

- **db data processed (gran.)** downloads the data used for plotting, including
  the band's display granularity.
- **db data fullres** downloads the generated result before display granularity
  is applied. Filters still apply: a CPM result contains per-minute counts,
  for example, rather than individual pulse records.

The generated CSVs and metadata are stored under `csv-files/`, in a directory
corresponding to the panel's filename and folder location.

## Save a snapshot

Once the graphs have finished loading successfully, enter a **snapshot label**
and click **create snapshot**. This copies the panel's generated CSVs and query
metadata into a dated snapshot folder. The **Available Snapshots** list updates
after creation; expand an entry to inspect its files and queries.

The generator can reuse matching snapshots on later requests. If a matching
snapshot covers only the earlier part of a time range, it can combine it with
newer database results. The graph's diagnostic information reports snapshot use.
Snapshots preserve generated results; they do not copy the database or save a
separate version of the panel configuration.

**delete all snapshots** removes the saved snapshot folders for the current
panel after confirmation. Database measurements and the panel JSON remain.

## Optional waveform links

If an administrator has configured a separate waveform viewer, eligible
`cps(...)`, `cpm(...)` and `spectrum(...)` bands have **waveforms** links in their
legends. Count-rate links use the graph's current visible time window; spectrum
links use the band's configured time range. The waveform viewer and its original
detector files are a separate installation. See
[waveform configuration](configuration.md#waveform-links).

## View prepared panels

An installation with `read_only = true` displays previously generated graph
CSVs. The panel editor, CMS and snapshot-management controls are unavailable.
Opening or refreshing a panel in that mode reads the prepared results.

Access to the homepage and to individual panels is configured separately by the
web server. Public deployments may provide only direct panel links. See
[deployment and access control](configuration.md#deployment-and-access-control)
for those installation options.

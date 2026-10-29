# Configuration

For a new Ubuntu server, start with the [installation guide](../deploy/README.md).
The installer creates the viewer configuration and credentials, sets file
permissions, and configures HTTPS and website authentication. This reference
covers later configuration changes, optional integrations and manual deployments.

## Settings files

Paths below are relative to the repository folder unless shown as absolute.
Private settings and runtime data are ignored by Git.

| File | Purpose | Ubuntu installer behaviour |
| --- | --- | --- |
| `deploy/settings.yml` | Hostname, optional URL path, installation accounts, database name/port and initial timezone. | You create it from `deploy/settings.example.yml` before installation. |
| `config.ini` | Read-only mode, graph generation limit, homepage timezone, waveform viewer and custom homepage links. | Created initially; subsequent local changes are preserved. |
| `psql_credentials.py` | Database connection used by PHP's table list and Python's graph generator. | Generated using the configured database viewer account. |
| `/etc/ida-data-viewer/cms/credentials.php` | CMS username and password hash. | Created initially; later account changes use the CMS administration page. |
| `/etc/ida-data-viewer/website.htpasswd` | Website authentication. | Managed from the private deployment settings. |

The [installer guide](../deploy/README.md#accounts-and-files) describes account
roles and which settings are reconciled on repeated runs. It also accepts a
private settings-file path outside the checkout.

The installer uses `ida_hostname` for the domain or subdomain and `ida_url_path`
for the optional URL path (`/` by default). For example, the hostname
`viewer.example.org` with `ida_url_path: /data` serves the viewer at
`https://viewer.example.org/data/`. To change the URL path, update the private
installation settings and rerun the installer. Filesystem locations stay the
same. See the [installation guide](../deploy/README.md#2-configure-and-run-the-installer).

## Viewer settings

Edit `config.ini` in the repository folder. For manual setup, first copy
[config.example.ini](../config.example.ini) to `config.ini`. For an editable
installation, the basic settings are:

```ini
[viewer]
read_only = false
display_timezone = "UTC"
max_graph_generators = 2

[waveforms]
url = ""
index_table = "root_files"
```

### Read-only mode

- `read_only = false` enables the panel editor and live graph generation. Use
  it behind website authentication, as configured by the Ubuntu installer.
- `read_only = true` displays prepared CSVs without running the graph generator.
  Panel creation/saves, snapshot creation/deletion and OneFileCMS access are
  rejected, and their controls are omitted from the page.

The setting applies to the whole installation, independently of its hostname.
Changes take effect on the next request, with no server restart. An already
open editor may still show controls after read-only mode is enabled, but its
writes will be rejected. Database administration through Adminer is separate.

`read_only` controls editing and generation; it does not configure website
password protection. The dedicated read-only database account separately
restricts SQL operations. See [deployment and access control](#deployment-and-access-control).

`config.ini` is ignored by Git. Missing or invalid configuration returns HTTP
503. The installer blocks HTTP downloads of the file; manual web-server setups
must apply the credential-file restrictions described below.

### Homepage timezone

`display_timezone` controls how the homepage interprets and displays table
timestamps, including freshness colouring. For timestamps without a timezone,
match the timezone in which the measurements were recorded. The installer uses
`ida_timezone` from `deploy/settings.yml`, initially `UTC`. If
`display_timezone` is omitted from `config.ini`, the application default is
`America/New_York`.

The timezone also determines today's date when setting the initial start time
of a newly created panel. Changing it does not convert stored records or saved
panel time windows, or change graph axes and waveform time ranges. Keep
measurement timestamps and panel time windows consistent; see
[the data guide](data-and-filters.md#table-structure).

### Graph generation limit

`max_graph_generators` limits how many live graph generators may run at once
across all users and browser tabs of this installation. It defaults to `2`,
including when omitted from an existing `config.ini`, and accepts integers from
`1` to `32`. If all slots are busy, graph requests return HTTP 202 with
`X-Graph-Generation: waiting` and `Retry-After: 2` without starting Python.
The browser retains the loading indicator and retries after roughly two seconds,
releasing its connection and the PHP worker between attempts. The **query**
diagnostic page also retries automatically when all slots are busy.

Change this setting while no graphs are loading; an active semaphore pool can
retain its previous limit until running requests finish. No server restart is
needed. The limit applies to both normal graph requests and the **query**
diagnostic link. Prepared CSV reads in read-only mode do not use the queue.
Direct command-line runs of `generate-graph-data.py` are separate from this
web-request limit.

The queue uses PHP's `sysvsem` extension, enabled by the Ubuntu installer. If
semaphore support is unavailable, the request returns HTTP 503 without starting
Python.
The limit controls concurrent generators; memory use still depends on each
graph's data volume and processing.

The homepage's [cancellation control](using-viewer.md#cancel-graph-generation)
stops running jobs and discards waiting requests. PHP uses `proc_open` to own
each Python child and a private pipe to request cancellation of its current
database operation. These process functions must be enabled alongside `sysvsem`.
Cancellation state is stored in a private directory within PHP's temporary
directory, shared by this installation's web workers. No extra configuration
is needed. Generated files are replaced only after each complete write.

## Waveform links

Set `[waveforms] url` to the full HTTP(S) address of your separately installed
waveform viewer. `index_table` names the table that viewer uses to locate the
original ROOT files; its default is `root_files`. An empty or omitted URL hides
all waveform links. The Ubuntu installer leaves the URL empty.

When enabled, `cps(...)` and `cpm(...)` bands link to the current visible time
window. `spectrum(...)` bands link to their configured time window when both
bounds are available. Other filters do not receive waveform links. The separate
viewer needs access to its original detector files and index table.

## Custom homepage links

Add sections to `config.ini` with unique names beginning with `homepage_link.`.
They appear after the panel list, in the order written:

```ini
[homepage_link.reports]
title = "Experiment reports"
label = "Open reports"
url = "https://example.org/reports/"
description = "Reports and supporting documents for this installation."
```

The example renders as:

> ### Experiment reports
>
> [Open reports](https://example.org/reports/)
>
> <small>Reports and supporting documents for this installation.</small>

`title`, `label` and `url` are required; `description` is optional. Text fields
use plain text. Relative URLs such as `reports/` point to tools in or alongside
the viewer, and full HTTP(S) URLs can point to other sites. An empty URL hides
the section. Omitting these sections adds no custom links.

## Database configuration

Each installation uses one PostgreSQL database with TimescaleDB. The Ubuntu
installer generates `psql_credentials.py` with its dedicated read-only viewer
account. Editing panels and creating snapshots writes files rather than
database records.

For a manual installation, copy
[psql_credentials.example.py](../psql_credentials.example.py) to the private
`psql_credentials.py` file alongside `generate-graph-data.py`:

```sh
cp psql_credentials.example.py psql_credentials.py
```

Set `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` and `PGPASSWORD` to your connection
details. Choose a database name and set `PGDATABASE` to it; it applies to every
panel. Use a dedicated read-only database account and keep the file out of Git.

Both the PHP table list and Python generator use this file. Define all five
variables as simple assignments: quote strings and use a number or quoted
number for `PGPORT`. `PGPASSWORD` may be explicitly empty for passwordless
authentication. Missing or unreadable settings are reported without falling
back to another credentials file or environment connection values.

The homepage's **manage tables** link uses the same host, port and database.
Adminer prompts separately for its username and password; neither is included
in the link. Use an account with the permissions required for the intended
administration. See the [Adminer reference](../postgres/README.md).

Each band's **Device** identifies a table in that database. Panel folders are
for organisation, including nested and archive folders. Database selection is
not taken from folder names, URL parameters or panel JSON fields. See
[data records and filters](data-and-filters.md) for the measurement-table format.

Prepared CSVs and snapshots belong to the database they were generated from.
To view another database, use a separate installation with its own credentials,
CSVs and snapshots. Switching `PGDATABASE` in place requires separating the
previous database's prepared CSVs and snapshots before generating results.

## Local data files

Panel definitions live under `json-files/`, with extensionless or `.json`
filenames and optional nested folders. Generated graph CSVs, full-resolution
results, query metadata and snapshots live under `csv-files/`. These directories
are ignored by Git; keep them when updating an installation. Their absolute
filesystem paths are derived from the application source location.

### Table visibility files

The homepage records table states in `inactive_tables.txt` and
`archived_tables.txt` beside `index.php`. Each file contains one table name per
line. Both are ignored by Git and are created or updated by the homepage
controls. A missing file means an empty list.

An inactive table appears in its own section without freshness monitoring.
An archived table is hidden from both lists. To restore an archived table,
remove its line from `archived_tables.txt` and reload the homepage; it returns
to the active list unless also listed in `inactive_tables.txt`. These files do
not alter database tables or affect queries from panels.

## Manual installation

If you are configuring an existing server yourself, supply Apache/PHP,
PostgreSQL with TimescaleDB, and Python with pandas and psycopg2. PHP needs its
PostgreSQL, mbstring and sysvsem extensions, and graph requests must be able to run
`python3`. PyYAML is also needed to run the example-data seed script.
The [installer's package list](../deploy/ansible/tasks/packages.yml) records
its Ubuntu 24.04 stack.

Create the database, enable TimescaleDB and provision appropriate accounts.
Create `config.ini` and `psql_credentials.py` as described above, and configure
[OneFileCMS credentials](../onefilecms/README.md) if using the CMS. Apache/Python
must be able to read the required settings. The web-server account needs write
access to `json-files/`, `csv-files/` and the locations used for the homepage's
visibility files and CMS runtime files.

Configure HTTPS, website authentication and restrictions on private files in
your web server. The browser also needs access to the public CDNs used for
JavaScript and CSS libraries in `load-panel.php`.

## Deployment and access control

| Deployment | `read_only` | Web-server access |
| --- | --- | --- |
| Private editing installation | `false` | Require a login for the whole installation. |
| Private installation viewing prepared data | `true` | Keep the login; no panel editing or live graph generation. |
| Public links to selected prepared panels | `true` | Make approved panel files public; block catalogue and administration endpoints. |

Keep editable installations behind authentication. OneFileCMS has its own login,
but it does not protect the panel editor, graph generator or direct JSON/CSV
URLs. The Ubuntu installer creates the private editing deployment. It enforces
access in its Apache virtual host with `.htaccess` overrides disabled; changing
`.htaccess` files does not change that installed profile.

### Manual Apache authentication

For manual deployments that enable `.htaccess`, the repository's root
[.htaccess](../.htaccess) requires HTTP Basic authentication for the installation,
including JSON and CSV files:

```apache
AuthType Basic
AuthName "Restricted Access"
AuthUserFile /etc/apache2/.htpasswd
Require valid-user
```

Set `AuthUserFile` to an absolute path outside the document root. Create a
**new password file only** with:

```sh
sudo htpasswd -cB /etc/apache2/.htpasswd viewer-user
```

The command prompts for a password. To add or update a user in an existing
file, omit `-c`, which otherwise replaces it. Apache must be able to read the
file. The root `.htaccess` also blocks downloads of `config.ini` and
`psql_credentials.py`; the CMS's [.htaccess](../onefilecms/.htaccess) protects
its credentials, logs and login-attempt counter.

Apache must honour the files and permit their directives through `AllowOverride`
or `AllowOverrideList`. Other servers, including PHP's built-in development
server, need equivalent access and credential-file restrictions. See Apache's
[authentication guide](https://httpd.apache.org/docs/2.4/howto/auth.html) and
[.htaccess guide](https://httpd.apache.org/docs/2.4/howto/htaccess.html).
Use HTTPS, and verify in a fresh browser session that panel pages and direct
JSON/CSV URLs require a login.

### Public links to selected prepared panels

Use a separate public installation with `read_only = true`. Generate the graphs
on the private editing installation first, then copy the approved panel JSON,
prepared CSVs/metadata and required viewer code/assets. The panel-viewing
endpoints do not need database credentials in this mode. Keep SQL/CMS
credentials, Adminer, deployment tools, `.git`, logs and backups out of the
public deployment.

Read-only mode does not hide the homepage or panel catalogue, or disable the
homepage's table activation/archive actions. Restrict those endpoints through
the web server. The following Apache 2.4 example is for a **separate manual public
deployment with `.htaccess` enabled**; it replaces that deployment's root rules.
It is not applied by the Ubuntu installer.

```apache
Options -Indexes
Require all granted

<FilesMatch "(?i)\.php$">
  Require all denied
</FilesMatch>
<FilesMatch "^(?:load-panel|call-pyscript)\.php$">
  Require all granted
</FilesMatch>

# Retain credential-file protection when replacing the private rules.
<FilesMatch "^(?:config\.ini|psql_credentials\.py)$">
  Require all denied
</FilesMatch>
```

Apache must permit these directives, including `Options -Indexes`. If your
server enforces access in virtual-host configuration instead, configure the
equivalent rules there. The profile allows the two PHP entry points for viewing
prepared panels and blocks other PHP endpoints, including the homepage,
catalogue, CMS, snapshot/save endpoints and Adminer. PHP can still include helpers.

Check that a shared `load-panel.php?filename=...` link and its graph requests
work without a login, while catalogue, administration and private-setting URLs
are denied. The web-server process only needs read access to published JSON/CSVs.

Panels published this way are unlisted. Anyone who obtains or guesses their
URLs can access and forward the published JSON, CSVs and metadata, including
any full-resolution results or snapshots copied there. Publish only material
intended for that audience. Keep authentication or explicit access rules for
panels restricted to particular people.

## Request handling and reverse proxies

Panel creation/saves, snapshot creation/deletion, homepage table controls and
OneFileCMS POSTs require an `Origin` header matching the viewer's scheme, hostname and port.
Browsers provide it for normal POSTs. Missing, `null`, malformed or different
origins return HTTP 403 before the action runs. The expected origin is derived
from PHP's `HTTP_HOST` and `HTTPS` values. Command-line POST clients must supply
the matching `Origin` along with their existing login.

Before starting Python for a live graph request, `call-pyscript.php` checks
`Sec-Fetch-Site`. It allows `same-origin` and rejects `cross-site` and `same-site`,
including other subdomains. The literal value `none` is allowed for GET navigation
to a top-level page, so typed and bookmarked query URLs work with those headers.
If the header is missing, a valid `Referer` must match the viewer's scheme,
hostname and port. Otherwise the request returns HTTP 403. Explicitly rejected
or unknown header values do not fall back to the referrer.

HTTPS and localhost browsers normally supply Fetch Metadata. Plain HTTP on
other hosts uses the referrer fallback. Authenticated command-line clients
requesting graph generation must supply matching request headers. Read-only
mode serves prepared CSVs without the generation check.

Panel responses also set `Content-Security-Policy: frame-ancestors 'self'` and
`X-Frame-Options: SAMEORIGIN`, permitting frames from the same origin. Ordinary
top-level links to panels work across websites.

Behind a reverse proxy, ensure PHP receives the public hostname, port, HTTPS
status and URL path. Client-supplied forwarded headers are not trusted
automatically. These values also determine diagnostic metadata links.

## Generator diagnostics

The graph's **query** link shows generator diagnostics in editable mode.
PHP passes the request's base URL to Python with `--base-url`, so printed
metadata links follow the installation's hostname and URL path.

To generate graph 1 of the example panel directly with diagnostic output, run
from the repository folder:

```sh
python3 generate-graph-data.py "example-panel" 1 1
```

Direct Python runs without `--base-url` print a relative metadata URL. They
use the same private database credentials and data directories as web requests.

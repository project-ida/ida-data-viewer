# Ubuntu installation

Install the viewer on a **fresh, dedicated Ubuntu 24.04 amd64 server**, physical
or virtual. The installer configures Apache/PHP, Python, PostgreSQL 16,
TimescaleDB, Let's Encrypt HTTPS and website authentication on the same machine.
It creates the database and accounts, and loads
[synthetic measurements and an editable example panel](#synthetic-data-reference)
so there is data to explore immediately.

For a tour after installation, see [Using the viewer](../docs/using-viewer.md).
For existing servers and configuration details, see
[Configuration](../docs/configuration.md).

## 1. Prepare the server

You need:

- A fresh Ubuntu 24.04 amd64 installation and a normal user with sudo access.
- Git to clone this repository, and Internet access to download packages.
- A public hostname whose DNS records point to the server.
- Inbound ports **80 and 443** allowed by the server/provider firewall.

If you publish an IPv6 DNS record, that address must reach this server too.
Let's Encrypt's [HTTP-01 validation](https://letsencrypt.org/docs/challenge-types/#http-01-challenge)
requires public HTTP access. The installer does not change DNS or firewall rules.

Clone this repository into a folder in your home directory, for example
`/home/alex/ida-data-viewer`, and open that folder in your terminal. Apache will
serve this checkout directly. The installation user and absolute application
path are derived from its owner and location; there is no separate copy under
`/var/www` to maintain.

## 2. Configure and run the installer

From the repository folder:

```sh
cp deploy/settings.example.yml deploy/settings.yml
chmod 600 deploy/settings.yml
```

Edit **`deploy/settings.yml`** using [the example](settings.example.yml):

| Settings | What to choose |
| --- | --- |
| `ida_hostname`, `ida_letsencrypt_email` | The public hostname and certificate contact email. |
| `ida_url_path` | Optional URL path: `/` (default), `/data`, or a nested path such as `/tools/data`. |
| `ida_database_name`, `ida_database_port` | The application database name and local PostgreSQL port. |
| `ida_timezone` | The timezone for your measurement timestamps; defaults to `UTC`. |
| Database administrator and viewer accounts | Separate database usernames and passwords for administration and read-only queries. |
| Website and CMS accounts | A website login and a separate CMS login for managing panel files. |

Replace every example password. Passwords must contain 12–72 printable ASCII
characters without spaces. The settings file is private and ignored by Git;
keep it for later installer runs. No other configuration files need to be
filled out before running the installer.

For example, these settings serve the viewer at `https://viewer.example.org/data/`:

```yaml
ida_hostname: viewer.example.org
ida_url_path: /data
```

Use your own hostname in place of `viewer.example.org`. The hostname can be a
domain or subdomain; do not include `https://` or a path in `ida_hostname`.
URL-path segments may contain letters, numbers, underscores and hyphens. A
trailing slash is optional in the setting. Omitting `ida_url_path` serves the
viewer at `/`. This URL mapping does not move the checkout or its data files.
The installer still manages a dedicated server; URLs outside a configured
subpath are denied.

```sh
./deploy/install.sh
```

The launcher requests sudo, installs Ansible in an isolated environment under
`/opt/ida-deploy`, and runs the playbook locally. It installs the services,
creates the database and configuration files, obtains the HTTPS certificate,
loads the example data and panel, and checks the resulting installation.
The automated certificate request uses
the configured contact email and accepts the Let's Encrypt subscriber agreement.

You do not need to install Ansible yourself, configure a remote controller,
or edit Apache files in system directories. To use a different private
settings-file path, pass it as the launcher's only argument. Keep differently
named settings files outside the checkout.

## 3. Open the viewer

Open the URL reported by the installer: `https://YOUR_HOSTNAME/` by default,
or `https://YOUR_HOSTNAME/data/` if you selected `/data`. The URL path is
independent of the checkout folder's name. Sign in using `ida_web_username`
and `ida_web_password` from your installation settings.

The homepage shows **Tables** and **Panels**. The two example tables appear and
`example-panel` is listed under **Panels**. Open
it to see the graphs, then click **show editor** to inspect their settings.
You can also open it directly at
`https://YOUR_HOSTNAME/load-panel.php?filename=example-panel`.
For a `/data` installation, use
`https://YOUR_HOSTNAME/data/load-panel.php?filename=example-panel` instead.

The example is historical, so its table rows normally appear red: their latest
timestamps are older than the homepage's 90-minute freshness threshold. The
seeded data are still available to plot.

The installer enables panel editing (`read_only = false`). Follow
[Using the viewer](../docs/using-viewer.md) for the homepage controls, copying
panels, editing, CSV downloads and snapshots. Browsers need access to the public
CDNs supplying the viewer's JavaScript and CSS libraries.

## Accounts and files

| Account | Purpose |
| --- | --- |
| Website | `ida_web_username` and its password protect the whole site, including Adminer and JSON/CSV downloads. |
| CMS | `ida_cms_username` and its password initialise a separate login for managing panel files. Later changes use the CMS administration page. |
| Database administrator | Owns the application database and can create tables through Adminer. It is not a PostgreSQL superuser and cannot create databases or roles. |
| Database viewer | Used by the viewer for queries. It can SELECT from public tables and defaults to read-only transactions. |

Tables created in `public` by the configured database administrator automatically
grant SELECT to the viewer. Tables created by another owner or in another schema
need appropriate grants. Configure ingestion accounts when setting up your own
measurements and their data-writing process; see the
[data guide](../docs/data-and-filters.md#table-structure).

The installer generates `config.ini` and `psql_credentials.py` in your checkout,
owned by you and readable by Apache/Python. HTTP downloads of both are blocked.
You can edit `config.ini` as your normal user to change the
[viewer settings](../docs/configuration.md#viewer-settings).

CMS credentials and the website password file live under `/etc/ida-data-viewer`,
outside the document root. The installer/CMS manage them. Private deployment
settings, `deploy/` and hidden checkout directories, including Git metadata,
are denied through the website, even after login.

The installer grants Apache traversal through the checkout's home-directory
parents without directory-listing access. Panel JSON and generated CSV
directories are writable by the web-server account. The checkout and CMS
directories also permit runtime files for table visibility and login counters;
their sticky bit protects application filenames from replacement by PHP.
Setgid data directories and Apache's shared-file umask retain local-user access
to generated files.

The virtual host enforces access directly with `.htaccess` overrides disabled.
It permits PHP requests only at installed entry-point URL paths and disables
PHP in the data directories. See [deployment and access control](../docs/configuration.md#deployment-and-access-control)
for manual and public prepared-panel deployments.

## HTTPS and installed services

The installer starts an HTTP challenge endpoint, obtains a certificate with
Certbot's webroot method, then enables the authenticated HTTPS website. Other
HTTP requests redirect to HTTPS. The challenge endpoint remains public for
renewal and serves only the separate ACME challenge directory.

Apache reads the certificate and key from
`/etc/letsencrypt/live/YOUR_HOSTNAME/`. Certbot's systemd timer handles renewal;
a deployment hook validates the Apache configuration and reloads it after a
successful renewal. Certificate paths do not need to be supplied manually.

If initial issuance fails, correct the DNS or port-access problem reported by
Certbot and rerun the installer. HTTPS is enabled after the certificate and key
exist, without a self-signed fallback.

The stack uses Apache with PHP 8.3, Python 3.12, PostgreSQL 16 and TimescaleDB.
Python dependencies come from Ubuntu packages. PostgreSQL and TimescaleDB use
the official [PostgreSQL](https://www.postgresql.org/download/linux/ubuntu/) and
[TimescaleDB](https://www.tigerdata.com/docs/self-hosted/latest/install/installation-linux/)
APT repositories, selecting their current candidate versions for the configured
package names. See the [package list](ansible/tasks/packages.yml).

PostgreSQL listens only on `127.0.0.1` at the configured port. Local operating-system
administration uses the `postgres` account; application TCP connections require
SCRAM passwords. Waveform links start disabled and can be enabled through
[configuration](../docs/configuration.md#waveform-links).

## Updates and diagnostics

The installer refuses to adopt an existing PostgreSQL configuration or private
viewer settings without its own installation record. Start from a fresh VM and
checkout. An interrupted installation can be resumed by rerunning it.

Keep the original hostname, website username, database name, database accounts,
port, checkout path and owner on repeated runs. Update source code with Git as
your normal user, then rerun the installer to reconcile access permissions and
managed server configuration.

To change the viewer's URL path, update `ida_url_path` in the private installation
settings and rerun the installer. Update any saved links to the new path. Panel
files and database contents stay in the same locations.

Panels, generated CSVs, table visibility and database contents are retained.
`config.ini` and CMS credentials are created only if absent, preserving later
changes. Database and website passwords are reconciled to the private
installation settings. The installer never drops the database.

Example setup runs until its first successful completion, preserving existing
example tables and panel edits when resuming an interrupted installation. After
that, installer reruns skip it. You can delete the example tables or panel when
you no longer need them; subsequent installer runs do not recreate them.

The final checks verify database connectivity, TimescaleDB, the viewer account's
restrictions, the certificate's chain/hostname, website authentication and denial
of private-file and checkout-metadata downloads.

Apache/PHP errors go to `/var/log/apache2/ida-data-viewer-error.log`. If the site
or CMS cannot load, inspect that log on the server. For a graph-generation error,
open the graph's **query** link in the viewer for diagnostics.

## Synthetic data reference

The installer runs [seed-demo.py](seed-demo.py) automatically using the private
deployment settings. It creates:

- `public.example_pressure`: six days of pressure readings, one per second.
- `public.example_pulses`: synthetic gamma/neutron pulses over the same period,
  including three five-minute neutron bursts.
- `json-files/example-panel`: an editable panel with pressure, gamma CPM,
  neutron CPM and energy-spectrum graphs.

The viewer has SELECT access to both tables. New example tables are bulk-loaded
together in one transaction, without waiting between their timestamps. Existing
tables and panel edits are preserved. The default data window starts at
`2026-01-01 00:00:00` and covers six days. You can delete the example tables
through Adminer and the panel through the CMS when you no longer need them.

No separate seed command is needed for installation. The commands below are
for manual data creation, inspection and export. Manual seeding stops if a table
already exists. Use `--settings /path/to/settings.yml` for a different private
settings-file path, or `--start '2026-02-01 00:00:00'` to shift the data window.
CSV export and panel-only commands do not connect to a database.

### Panel creation

The seed script uses the committed [example-panel.json](example-panel.json)
template and writes it to `json-files/example-panel`, with no filename extension,
matching panels created from the homepage. The panel covers the
complete six-day data window, with a start just before the first sample so that
all records fall inside the viewer's exclusive query boundaries. The panel
has these filters:

| Graph | Filter |
| --- | --- |
| Pressure over time | `singlechannel(1)`; **Gran. 60** initially displays every 60th reading. |
| Gamma counts per minute | `cpm(<0.22,>0)`; PSP below 0.22, with no additional energy cut. |
| Neutron counts per minute | `cpm(>0.22,>500)`; PSP above 0.22 and energy above 500. |
| Pulse energy spectra | `spectrum(<0.22,>0)` and `spectrum(>0.22,>500)`. |

If both tables were seeded separately, create the JSON with:

```sh
python3 deploy/seed-demo.py --panel-only
```

This can also create a panel for inspection without a database. Use the same
`--start` value as the data. Existing panel edits are preserved. If writing the
panel fails after a successful database load, correct the file permissions and
use `--panel-only` to finish without reseeding.

### Pressure data

To create only `public.example_pressure`, run:

```sh
python3 deploy/seed-demo.py --dataset pressure
```

It creates a TimescaleDB hypertable with the [standard table structure](../docs/data-and-filters.md#table-structure)
and grants the viewer SELECT access. Each `channels` array contains one pressure
value in bar. There are **518,400 records: six days at exactly one per second**.
Pressure starts near 2 bar, rises to about 4.5 bar, drops to roughly 3 bar,
plateaus and falls to zero. The small measurement noise is repeatable. The
profile is illustrative and does not import experimental records or model
absorption physics.

For a CSV preview without database access or private credentials:

```sh
python3 deploy/seed-demo.py --dataset pressure --csv /tmp/example-pressure.csv
```

The file has `time,channels` columns and PostgreSQL array values such as
`{2.001234}`. Exports preserve existing files; choose a new filename for each
export. `--start` accepts a whole-second timestamp without a timezone suffix.
Use the same timezone as the other measurement records.

### Radiation pulse data

To create only `public.example_pulses`, run:

```sh
python3 deploy/seed-demo.py --dataset pulses
```

To export its CSV without a database connection:

```sh
python3 deploy/seed-demo.py --dataset pulses --csv /tmp/example-pulses.csv
```

Each row is one pulse, with **`[PSP, energy]`** in `channels`. Energy is an integer
digitizer value from 50 to 3999, without calibration into keV. `time` contains
the timestamp with microseconds; `ps BIGINT` stores the fractional second in
picoseconds, matching the CAEN ingestion format. CSV columns are `time,channels,ps`.

The illustrative model follows shapes in the
[PSD analysis notebook](https://github.com/project-ida/arpa-e-experiments/blob/main/tutorials/PSD-analysis.ipynb).
Energies follow falling exponential distributions and PSP is Gaussian at each
energy. The lower gamma band rises from roughly 0.15 towards 0.18; the higher
neutron band starts near 0.3 and bends towards 0.22. Both bands narrow with
increasing energy. These are synthetic values rather than a fitted detector model.

| Component | Average rate before PSP/energy cuts |
| --- | --- |
| Neutron background | 0.035/second, or 2.1/minute. |
| Gamma background | 0.35/second, or 21/minute: ten times the neutron background. |
| Neutrons during each burst | 0.7/second, or 42/minute: twenty times the background neutron rate in total. |

The approximate neutron background comes from the saved counts-per-minute trace
in the [radiation pulse analysis notebook](https://github.com/project-ida/arpa-e-experiments/blob/main/tutorials/Radiation-pulse-analysis.ipynb).
That trace uses a PSP threshold near 0.207 and energy above zero. The gamma ratio
and PSD distributions here are illustrative choices.

Three bursts start **24, 60 and 108 hours** after the first timestamp and each
lasts **five minutes**. Burst-neutron energies use a harder exponential
distribution starting at **501**, with a scale of 900 and an upper limit of
3999. Gamma and background-neutron distributions remain unchanged. Exponential
waiting times produce Poisson counts within each constant-rate interval.
The fixed random seeds make the roughly 200,000 pulse records reproducible.

The `>500` energy cut removes many background neutrons while retaining all burst
neutrons before the PSP cut. The filtered burst-to-background ratio can therefore
exceed 20. Overlapping PSP distributions mean threshold selections identify
candidates rather than perfectly separating the generated populations. See the
[filter reference](../docs/data-and-filters.md#counting-events-cps-cpm-and-cph)
for other count intervals and spectrum options.

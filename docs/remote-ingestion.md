# Sending measurements from other computers

The [Ubuntu installer](../deploy/README.md) sets up the database, example data,
administrator, acquisition and viewer accounts, and TLS certificate renewal.
PostgreSQL initially accepts only local connections. To send measurements from
other computers, configure remote access to suit your laboratory network, VPN
or Internet-facing server using the steps below.

An acquisition program connects to PostgreSQL and inserts rows; it does not
upload measurements through the viewer's website. Use the
[data guide](data-and-filters.md#table-structure) for the required record format.

The examples below extend a completed Ubuntu installation. Replace these
example values with the names and port from your installation settings:

| Example | Meaning |
| --- | --- |
| `experiments` | Application database. |
| `5432` | Configured PostgreSQL port. |
| `ida_admin`, `ida_reader` | Administrator and viewer accounts from your installation settings. |
| `ida_acquisition` | Acquisition account for reading and adding records, configured by `ida_database_acquisition` and `ida_database_acquisition_password`. |
| `viewer.example.org` | Database server's certificate hostname; this can be the website hostname. |

The commands use PostgreSQL port `5432`. If you chose another port during
installation, replace `5432` in the commands and Python example below.

Create measurement tables in `public` using the administrator specified by
`ida_database_admin` in your deployment settings, either programmatically or
through Adminer. The installed acquisition account can read and insert into
existing tables and any new ones created by that administrator, including using
generated record IDs. It cannot update/delete existing records or create,
alter or drop tables. The viewer has read access to these tables. No per-table
grants are needed for this
workflow; see the [table-structure example](data-and-filters.md#table-structure).

## 1. Configure remote connections

Run these steps **on the database server after the installer has finished**.

These instructions allow a computer at any address to connect using the acquisition
username and password, with TLS encryption required. The installer handles
certificate setup and renewal automatically. Acquisition computers do not need
their own domain names or certificates.

### Allow PostgreSQL to receive remote connections

Paste this into the server terminal:

```sh
sudo tee /etc/postgresql/16/main/conf.d/zz-remote-ingestion.conf > /dev/null <<'CONF'
listen_addresses = '*'
CONF
sudo chmod 0644 /etc/postgresql/16/main/conf.d/zz-remote-ingestion.conf
```

This creates a configuration file telling PostgreSQL to accept connections on
all of the server's network addresses. It does not remove password protection.
When you restart PostgreSQL below, it reads this file and applies the setting
instead of the installer's local-only setting.

### Allow the acquisition account to connect

Open PostgreSQL's connection rules in the Nano text editor:

```sh
sudo apt-get install nano
sudo nano /etc/postgresql/16/main/pg_hba.conf
```

Paste these lines **before the existing `host` rules**, replacing `experiments`
and `ida_acquisition` if you chose different names:

```text
hostssl experiments ida_acquisition 0.0.0.0/0 scram-sha-256
hostssl experiments ida_acquisition ::/0      scram-sha-256
host    all         ida_acquisition 0.0.0.0/0 reject
host    all         ida_acquisition ::/0      reject
```

The first two lines allow the acquisition account to connect from any IPv4
or IPv6 address, using its password and TLS. The last two reject other
connections as that account, including unencrypted connections. Keep the
existing rules for local administrator and viewer access.

Save with **Ctrl+O**, press **Enter**, then exit with **Ctrl+X**.

### Check and apply the settings

Back in the server terminal, paste this command to check the connection rules:

```sh
sudo -u postgres psql -X -v ON_ERROR_STOP=1 \
  -h /var/run/postgresql -p 5432 -d experiments -P pager=off \
  -c "SELECT line_number, error FROM pg_hba_file_rules WHERE error IS NOT NULL;"
```

It should show **`(0 rows)`**, meaning no file syntax errors were found. If it
lists errors, correct those lines in Nano before continuing.

Then restart PostgreSQL and check the listener setting:

```sh
sudo systemctl restart postgresql@16-main
sudo -u postgres psql -X -h /var/run/postgresql -p 5432 -d experiments \
  -c "SHOW listen_addresses;"
```

The setting should now be `*`. Restarting briefly disconnects existing database
sessions.

## 2. Permit the network traffic

The firewall must also allow the database port. On the server, the following
commands allow SSH on `22`, the website and certificate renewal on `80` and
`443`, and PostgreSQL on `5432`. If you use a different SSH or database port,
change those numbers before running these commands:

```sh
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 5432/tcp
sudo ufw default deny incoming
sudo ufw enable
sudo ufw status verbose
```

If your hosting provider or institution has another firewall, allow the same
ports there as well. Opening the database port lets computers reach PostgreSQL;
the connection rules above still require the acquisition account's password and TLS.

### Restricting access to particular computers

The starting configuration above allows connection attempts from any address.
Where practical, restrict the database port to your acquisition computers or
laboratory/VPN network in both the firewall and `pg_hba.conf`. Replace the
`0.0.0.0/0` and `::/0` addresses in the **`hostssl` lines** with the allowed
client addresses or network ranges; keep the rejection lines. For a single
IPv4 computer, use its address followed by `/32`. For Internet connections,
use the public address the server sees, which may belong to the client's router.

`listen_addresses` chooses the server's own listening addresses. It does not
select which client computers are allowed to connect; use the connection rules
and firewall for that.

## 3. Connect from an acquisition computer

On the acquisition computer, use the server hostname, port, database name and
acquisition credentials. With the server configured as above, `psycopg2` negotiates
TLS encryption automatically.

Install the example's dependencies in your Python environment:

```sh
python3 -m pip install psycopg2-binary
```

Run this Python connection check, replacing the hostname, port and account
details with your own:

```python
import getpass

import psycopg2

conn = psycopg2.connect(
    host="viewer.example.org",
    port=5432,
    dbname="experiments",
    user="ida_acquisition",
    password=getpass.getpass("Database acquisition password: "),
    connect_timeout=10,
)
print("TLS active:", conn.info.ssl_in_use)
conn.close()
```

It should print **`TLS active: True`**. The server connection rules above reject
unencrypted acquisition connections.

Your acquisition program can use the same connection settings to insert into
tables created by the administrator. Keep its acquisition credentials in a private
configuration file, separate from the viewer's read-only credentials. The
[data guide](data-and-filters.md#table-structure) describes the record format.

## Maintaining this manual configuration

If you run the installer again, repeat
[Allow the acquisition account to connect](#allow-the-acquisition-account-to-connect) and
[Check and apply the settings](#check-and-apply-the-settings): the installer
replaces `pg_hba.conf`. Your acquisition account, table permissions, listener file and
firewall rules are retained. Certificate setup and renewal remain automatic.

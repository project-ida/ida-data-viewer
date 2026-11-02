# Adminer

The homepage's **manage tables** link opens the official Adminer 6.2.0 English
release. The server, port and database are filled in from the viewer's database
configuration. The optional `[adminer] username` setting in `config.ini` selects
the administration account; the installer initially uses `ida_database_admin`.
With **Permanent login** enabled, returning through this link can resume that
account's saved login. Otherwise, Adminer prompts for its password. Leave the
username empty to choose the account on the login screen.

The private installation's Apache authentication also protects Adminer. PHP's
server configuration controls its logging, memory and upload limits.

See the [Adminer website](https://www.adminer.org/) for upstream documentation.

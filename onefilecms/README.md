# OneFileCMS credentials

The [Ubuntu installer](../deploy/README.md#accounts-and-files) creates the CMS
account and configures its private credentials file automatically. Use
**manage panels** on the homepage to open the CMS; see
[Copy and organise panels](../docs/using-viewer.md#copy-and-organise-panels)
for the normal workflow. The setup instructions below are for manual deployments.

The CMS loads its username, password hash and salt from `credentials.php` in this
directory. That private file is ignored by Git. The committed
`credentials.example.php` shows the format and contains no usable login.

For manual setup, create a credentials file with PHP:

```sh
php onefilecms/create-credentials.php
```

The command prompts for a username and password, hides password input on a Unix
terminal, generates a random salt, and stores only the compatible password hash.
It creates the file with owner-only permissions and refuses to overwrite an
existing file. The CMS uses its existing SHA-256 login format; a hash generated
with PHP's `password_hash()` is not compatible with that login flow.

Alternatively, copy `credentials.example.php` to `credentials.php` and populate
all three assignments with an existing OneFileCMS configuration. Keep
`$USERNAME` and `$HASHWORD` assignments at the start of their lines, because the
CMS's username/password change actions update those lines in the private file.

For production, keep the private file outside the web document root. Set the same
absolute path in `ONEFILECMS_CREDENTIALS_FILE` for the setup command and the PHP
web-server process. For example, after creating the private directory:

```sh
ONEFILECMS_CREDENTIALS_FILE=/etc/ida-data-viewer/cms/credentials.php \
  php onefilecms/create-credentials.php
```

PHP must be able to read the file. To change credentials through the CMS admin
page, PHP also needs write access to its parent directory for atomic updates:
the CMS writes the replacement to a private temporary file, then renames it
over the current file. Existing file permissions are preserved and failed saves
leave the current file intact. Keep that writable directory outside the document
root and leave application source files read-only. Configure environment variables in
your Apache/PHP-FPM deployment; setting one in an unrelated shell does not make
it available to the web-server process.

The CMS returns HTTP 503 if the file is missing, unreadable, malformed or has
empty/invalid credential fields. There is no built-in fallback account. The
provided `.htaccess` denies direct access to local credentials and temporary files
when Apache honors it. Other servers must apply equivalent access rules; an
external credentials file remains the preferred production arrangement.

## Session storage

The CMS uses PHP's configured session storage. For file-based sessions, configure
`session.save_path` outside the web document root in a directory writable by PHP
and restricted to the accounts that need access. The CMS Admin page displays the
effective location as **Session Path**.

## Error diagnostics

PHP errors are hidden during startup and login, and displayed after the CMS
session has been validated. Normal login messages, such as an incorrect password,
are still shown. Errors are also logged using the server's PHP logging configuration;
the CMS does not set a separate PHP error-log destination.

If the CMS cannot start or complete login, check the configured PHP/Apache/PHP-FPM
error log. The Ubuntu installer uses
`/var/log/apache2/ida-data-viewer-error.log`; manual setups may use
`/var/log/apache2/error.log` or another virtual-host/PHP-FPM destination. Keep
`display_errors=Off`, `display_startup_errors=Off` and `log_errors=On` in the
server's PHP configuration to cover failures before the CMS code can run.

The supplied `.htaccess` blocks direct downloads of CMS logs and the login-attempt
counter while allowing PHP to read and write them internally.

# Copy this file to psql_credentials.py and replace the example values.
# Keep psql_credentials.py private; it is ignored by Git.
# Use simple assignments: both PHP and Python read this format.

PGHOST = "localhost"
PGPORT = 5432
PGDATABASE = "your_database"
# Use a dedicated read-only database account for the viewer.
PGUSER = "your_readonly_user"
PGPASSWORD = "replace_with_your_password"

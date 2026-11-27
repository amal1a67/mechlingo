# Switching MechLingo to PostgreSQL / Neon

The application uses SQLite only when DATABASE_URL is unset. When a PostgreSQL URI is supplied, all requests use PostgreSQL. Connection or schema errors fail rather than falling back to SQLite.

## Before deploying
- Disable automatic deploys on the existing Render service until cutover is planned.
- Run the database compatibility checks on the PR. Do not merge on a failing check.
- Decide whether the current SQLite contents are disposable. Render Free has ephemeral storage; a new deployment or restart can destroy the current database. The migration script cannot retrieve a remote file. If current data matters, obtain a consistent SQLite backup privately from the running environment before any deploy. Do not commit the backup, which contains personal information and password hashes.
- Create an empty Neon project and database. Use the private connection string from Connect (keep TLS parameters). Never commit it or share it in chat. For the current direct psycopg implementation use a direct, non-pooled connection string.

## Existing data
With writes stopped, run locally or on a trusted machine with the current source SQLite snapshot:
1. Install requirements from this branch.
2. Set DATABASE_URL securely in that process's environment.
3. Run `python migrate_sqlite.py /path/to/consistent-backup.db`.

This initializes the empty destination without seed data, copies all current application tables in dependency order, preserves IDs and checks row counts, then adjusts identity sequences. It refuses to overwrite an existing populated target. Keep the original backup. Validate representative data and login before switching live traffic.

## New test database
If there is no data to retain, skip the migration. The application initializes tables and starter courses on first startup.

## Cutover
- Set DATABASE_URL on Render under Environment; use Save only until ready to deploy the compatible commit.
- If starting with an empty database, generate a new independent SECRET_KEY to invalidate old SQLite sessions before deployment; old numeric user IDs must not be trusted in the new database.
- Deploy the compatible commit. Confirm login, courses, a lesson, exam history, administrator pages, and persistence after restarting.
- For a fresh database, register the owner's account again and use the existing protected administrator setup flow with a fresh ADMIN_SETUP_TOKEN. Remove the setup token after activation and verify the administrator survives deployment.
- Keep production credentials and all backups outside GitHub. Create routine backups and verify restore separately.

## Test status
The PR includes a GitHub Actions smoke workflow for disposable SQLite and PostgreSQL 16. It does not substitute for testing the actual Neon connection, migration against a real backup, load testing, or legal review. Neon is suitable here for development; assess data residency and applicable privacy rules before storing real student data.

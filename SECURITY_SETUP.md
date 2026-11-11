# Credential configuration

Copy deploy/.env.example to .env and populate GEMINI_API_KEY, POSTGRES_PASSWORD and DATABASE_URL before starting Docker Compose. Never commit populated environment files. Docker Compose rejects missing or empty database settings.

Generate a distinct password (for example, with `openssl rand -hex 24`). Use the same password in POSTGRES_PASSWORD and the DATABASE_URL connection string; URL-encode it in the connection string. The GCP deployment script generates these settings automatically.

## Existing deployments

Changing POSTGRES_PASSWORD in .env does not change a password inside an existing PostgreSQL data volume. Coordinate a database password rotation and update all clients before restarting. Do not delete the data volume to rotate a password.

Treat any credential previously committed to a public repository as exposed. Revoke or rotate it in its issuing service and update deployments. Removing a value from current files does not invalidate it or remove historical copies. Confirm provider status and review usage separately.

## Local runs and branch upgrades

The application also requires DATABASE_URL when run outside Docker; it no longer falls back to a shared password. For a host-side command, use the database's host-accessible address rather than the Docker service name. Settings in the process environment take priority over .env.

Before applying this change to an existing deployment, set the required values to its current credentials. Removing defaults from code does not rotate a live database. Coordinate any rotation separately and keep a rollback plan.

Run the offline checks with `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_credential_configuration.py' -v`. These tests do not contact a database or an API provider.

For Render or another hosted service, explicitly configure DATABASE_URL in each application service. Verify the connection value points to the correct database; do not rely on a development default.

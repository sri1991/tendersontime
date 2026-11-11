"""Validate database configuration without supplying a shared password."""


def require_database_url(value: str | None) -> str:
    if not value or not value.strip():
        raise ValueError(
            "DATABASE_URL is required. Set it in the environment or .env "
            "using your own database credentials."
        )
    return value.strip()

"""Alembic environment (Orchestrix V2).

The database URL comes from application settings (settings.sync_database_url),
never from alembic.ini. Run Alembic from backend/ so `.env` is found.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.core.config import settings

# Importing the registry registers every model on Base.metadata.
from app.database.registry import Base

config = context.config

if config.config_file_name is not None:
    # Keep alembic.ini's logging setup, but do not disable loggers that
    # already exist when migrations run inside another process (e.g. tests).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

_REQUIRED_DRIVER = "postgresql+psycopg"


def _resolve_database_url() -> str:
    """Return the sync URL, failing clearly without ever echoing credentials."""
    url = settings.sync_database_url
    try:
        driver = make_url(url).drivername
    except (ArgumentError, ValueError):  # ValueError: e.g. a non-numeric port
        raise RuntimeError(
            "The database URL (DATABASE_URL / DATABASE_SYNC_URL) is not a valid "
            "SQLAlchemy URL."
        ) from None  # `from None`: the original message would contain the URL
    if driver != _REQUIRED_DRIVER:
        raise RuntimeError(
            f"Alembic needs the {_REQUIRED_DRIVER!r} driver but the resolved URL "
            f"uses {driver!r}. Use a postgresql+asyncpg:// DATABASE_URL, or set "
            "DATABASE_SYNC_URL to a postgresql+psycopg:// URL."
        )
    return url


# ConfigParser treats '%' as interpolation syntax; escape it. Reading the
# option back (get_main_option / get_section) returns the original URL.
config.set_main_option("sqlalchemy.url", _resolve_database_url().replace("%", "%%"))


def run_migrations_offline() -> None:
    """Emit migration SQL without connecting to the database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database with a throwaway connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        hide_parameters=True,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
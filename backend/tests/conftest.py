"""import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


TEST_DATABASE_URL = (
    "postgresql+asyncpg://postgres:postgres"
    "@localhost:5433/ai_workflow_db"
)

engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)

TestingSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest_asyncio.fixture
async def db_session():
    async with TestingSessionLocal() as session:
        yield session
        await session.rollback()"""

"""Pytest fixtures for Orchestrix (V2).

Safety model -- the database fixtures TRUNCATE tables, so they only run
against a database that passes ALL of these checks:

1. TEST_DATABASE_URL is set explicitly. There is no fallback to DATABASE_URL,
   .env or any V1 default.
2. Static checks on the URL (no connection needed): the postgresql+asyncpg
   driver, a database named like ``orchestrix[_x]_test``, never the V1
   database, and a local host unless that exact host is allowed through
   TEST_DATABASE_ALLOW_HOST.
3. Runtime check: the database itself carries the comment
   ``orchestrix-disposable-test-db`` (set once, by hand, when the test database
   is created). A name alone is never trusted.
4. Runtime check: the schema is at the Alembic head. This module never creates
   or changes schema; run ``alembic upgrade head`` yourself (the failure
   message prints the exact command).

Tests that need no database run without a live PostgreSQL, but
TEST_DATABASE_URL must still be set because the application settings are
loaded when test modules are imported.
"""

import os
import re
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

_BACKEND_DIR = Path(__file__).resolve().parents[1]

_URL_VAR = "TEST_DATABASE_URL"
_ALLOW_HOST_VAR = "TEST_DATABASE_ALLOW_HOST"
_REQUIRED_DRIVER = "postgresql+asyncpg"
_DB_NAME_PATTERN = re.compile(r"^orchestrix(_[a-z0-9]+)*_test$")
_FORBIDDEN_DB_NAMES = frozenset({"ai_workflow_db"})  # the V1 database
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_DISPOSABLE_MARKER = "orchestrix-disposable-test-db"

_MIGRATE_HINT = (
    'env -u DATABASE_SYNC_URL DATABASE_URL="$TEST_DATABASE_URL" '
    "SECRET_KEY=test-secret alembic upgrade head"
)

# Validated in pytest_configure, before any application module is imported.
_test_url: URL | None = None


def _location(url: URL) -> str:
    """host:port/database -- never includes credentials."""
    return f"{url.host}:{url.port}/{url.database}"


def _validated_test_url() -> URL:
    raw = os.environ.get(_URL_VAR, "").strip()
    if not raw:
        raise pytest.UsageError(
            f"{_URL_VAR} is not set. The test suite never falls back to "
            "DATABASE_URL or any other database. Example:\n"
            f"  export {_URL_VAR}="
            "'postgresql+asyncpg://postgres:PASSWORD@localhost:5434/orchestrix_test'"
        )
    try:
        url = make_url(raw)
    except (ArgumentError, ValueError):
        raise pytest.UsageError(f"{_URL_VAR} is not a valid SQLAlchemy URL.") from None

    problems: list[str] = []
    if url.drivername != _REQUIRED_DRIVER:
        problems.append(f"driver must be {_REQUIRED_DRIVER!r}, got {url.drivername!r}")

    name = url.database or ""
    if name in _FORBIDDEN_DB_NAMES:
        problems.append(f"{name!r} is the V1 database and must never be used for tests")
    elif not _DB_NAME_PATTERN.match(name):
        problems.append(
            f"database name {name!r} must look like 'orchestrix_test' or "
            "'orchestrix_<something>_test'"
        )

    host = url.host or ""
    extra_host = os.environ.get(_ALLOW_HOST_VAR, "").strip()
    if host not in _LOCAL_HOSTS and not (extra_host and host == extra_host):
        problems.append(
            f"host {host!r} is not local; if it really is your disposable test "
            f"server, set {_ALLOW_HOST_VAR}={host}"
        )

    if problems:
        raise pytest.UsageError(
            f"Refusing to use {_URL_VAR} ({_location(url)}): " + "; ".join(problems)
        )
    return url


def pytest_configure(config: pytest.Config) -> None:
    """Point the application at the test database before it is imported."""
    global _test_url
    _test_url = _validated_test_url()

    os.environ["DATABASE_URL"] = os.environ[_URL_VAR]
    # Alembic (alembic/env.py) prefers DATABASE_SYNC_URL; pin it to the same
    # database so a value from the developer's shell cannot redirect it.
    os.environ["DATABASE_SYNC_URL"] = _test_url.set(
        drivername="postgresql+psycopg"
    ).render_as_string(hide_password=False)
    os.environ["APP_ENV"] = "test"
    os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"  # never a shell value


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Drop the application's global engine pool if it was ever created.

    close=False discards pooled connections without awaiting anything, which is
    the only safe option here: they may belong to event loops that no longer exist.
    """
    module = sys.modules.get("app.database.session")
    engine = getattr(module, "engine", None)
    if engine is not None:
        try:
            engine.sync_engine.dispose(close=False)
        except Exception:  # noqa: BLE001 - cleanup must never change the test result
            pass


@pytest.fixture(scope="session")
def _verified_test_url() -> URL:
    """Connect once and verify the database is the disposable, migrated one."""
    assert _test_url is not None, "pytest_configure did not run"
    where = _location(_test_url)
    sync_engine = create_engine(
        _test_url.set(drivername="postgresql+psycopg"),
        poolclass=NullPool,
        hide_parameters=True,
    )
    connect_error: str | None = None
    try:
        with sync_engine.connect() as conn:
            marker = conn.execute(
                text(
                    "SELECT shobj_description(oid, 'pg_database') "
                    "FROM pg_database WHERE datname = current_database()"
                )
            ).scalar()
            current_heads = set(MigrationContext.configure(conn).get_current_heads())
    except SQLAlchemyError as exc:
        reason = str(getattr(exc, "orig", exc)).strip().splitlines()[:1]
        connect_error = (
            f"({type(exc).__name__}: {reason[0] if reason else 'no detail'})"
        )
    finally:
        sync_engine.dispose()
    if connect_error is not None:
        # Raised outside the except block so pytest shows only this message,
        # not the chained driver traceback.
        pytest.fail(
            f"Cannot query the test database {where} {connect_error}. "
            "Is PostgreSQL running and is TEST_DATABASE_URL correct?",
            pytrace=False,
        )

    if marker != _DISPOSABLE_MARKER:
        pytest.fail(
            f"Refusing to TRUNCATE {where}: the database is not marked as disposable. "
            "If it really is a throwaway test database, mark it once with:\n"
            f"  COMMENT ON DATABASE {_test_url.database} IS '{_DISPOSABLE_MARKER}';",
            pytrace=False,
        )

    alembic_config = Config(str(_BACKEND_DIR / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    alembic_config.set_main_option("path_separator", "os")  # silences a deprecation warning
    expected_heads = set(ScriptDirectory.from_config(alembic_config).get_heads())
    if current_heads != expected_heads:
        pytest.fail(
            f"The test database {where} is not at the Alembic head "
            f"(database: {sorted(current_heads) or 'not migrated'}, "
            f"code: {sorted(expected_heads)}). Migrate it with:\n  {_MIGRATE_HINT}",
            pytrace=False,
        )
    return _test_url


@pytest.fixture(scope="session")
def _test_engine(_verified_test_url: URL) -> AsyncEngine:
    # NullPool: no connection outlives its checkout, so it can never be bound
    # to a closed event loop (pytest-asyncio gives each test its own loop).
    return create_async_engine(_verified_test_url, poolclass=NullPool, hide_parameters=True)


async def _truncate_all_tables(engine: AsyncEngine) -> None:
    """Empty every table except alembic_version and restart identity counters."""
    async with engine.begin() as conn:
        # Fail fast instead of hanging if a leaked session still holds a lock.
        await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        result = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version' "
                "ORDER BY tablename"
            )
        )
        names = list(result.scalars())
        if names:
            quote = conn.dialect.identifier_preparer.quote_identifier  # always quotes
            await conn.execute(
                text(
                    "TRUNCATE TABLE "
                    + ", ".join(f"{quote('public')}.{quote(name)}" for name in names)
                    + " RESTART IDENTITY CASCADE"
                )
            )


@pytest_asyncio.fixture
async def db_session(_test_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session on a freshly emptied database.

    Truncation happens BEFORE the test, so a crashed earlier test cannot leak
    state and tests may freely call commit().
    """
    await _truncate_all_tables(_test_engine)
    factory = async_sessionmaker(
        bind=_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()  # discard anything the test left uncommitted
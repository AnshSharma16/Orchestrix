from collections.abc import AsyncIterator
from contextlib import suppress

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    hide_parameters=True,  # keep bind values out of error and echo output
    pool_pre_ping=True,  # detect stale connections before use
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT_SECONDS,
    pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield a session for one request. Never commits: services own commits."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except BaseException:
            # A failed rollback must not mask the original error; closing the
            # session below discards a broken connection either way.
            with suppress(Exception):
                await session.rollback()
            raise
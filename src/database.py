from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from src.config import settings

# If the database URL starts with postgres://, replace it with postgresql+asyncpg:// for async support
db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif db_url.startswith("sqlite:///"):
    db_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

# Create async engine. For sqlite, we specify check_same_thread=False
connect_args = {}
if "sqlite" in db_url:
    connect_args["check_same_thread"] = False
elif "postgresql+asyncpg" in db_url:
    # asyncpg does not support 'sslmode' as a query parameter in the URL.
    # We must strip it and configure SSL via connect_args instead.
    if "sslmode" in db_url:
        import urllib.parse
        parsed = urllib.parse.urlparse(db_url)
        query = urllib.parse.parse_qs(parsed.query)
        sslmode = query.pop("sslmode", [None])[0]
        new_query = urllib.parse.urlencode(query, doseq=True)
        parsed = parsed._replace(query=new_query)
        db_url = urllib.parse.urlunparse(parsed)
        
        if sslmode in ("require", "prefer", "allow"):
            connect_args["ssl"] = True

engine = create_async_engine(db_url, connect_args=connect_args, echo=False)

# Create async sessionmaker
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base()

# FastAPI dependency for getting database session
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

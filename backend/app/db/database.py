import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

from app.config import IGNORE_DOTENV, settings

# Skipped under the test suite's opt-out (see IGNORE_DOTENV in app/config.py):
# otherwise this copies every backend/.env key into os.environ, where
# pydantic-settings and any library reading the environment would find them.
if not IGNORE_DOTENV:
    load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in the environment variables.")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,
    # Per process, sized so backend + worker fit under Supabase's
    # 15-connection pooler cap - the budget is next to db_pool_size in
    # config.py. This used to be 10 + 20 overflow: 30 per process, 60 worst
    # case against a cap of 15.
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout_seconds,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

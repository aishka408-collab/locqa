import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Database path – stored in the project directory under db/
DB_DIR = Path(__file__).resolve().parent.parent / "db"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "qazaq_loc_qa.sqlite"

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}?check_same_thread=False"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db() -> Generator:
    """Yield a database session – FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

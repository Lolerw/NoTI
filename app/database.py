import os
import logging
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

logger = logging.getLogger("noti.database")
logging.basicConfig(level=logging.INFO)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./noti_turnos.db")
FALLBACK_SQLITE_URL = "sqlite:///./noti_turnos.db"

def init_db_engine():
    global DATABASE_URL
    # Normalizar string de PostgreSQL para psycopg 3 si es necesario
    db_url = DATABASE_URL
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif db_url.startswith("postgresql://") and not db_url.startswith("postgresql+"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    connect_args = {}
    if "sqlite" in db_url:
        connect_args["check_same_thread"] = False

    try:
        engine = create_engine(db_url, connect_args=connect_args)
        # Probar conexión simple
        with engine.connect() as conn:
            pass
        logger.info(f"Conexión exitosa a la base de datos principal: {db_url.split('@')[-1] if '@' in db_url else db_url}")
        return engine
    except Exception as e:
        logger.warning(f"No se pudo conectar a la base de datos principal ({db_url}): {e}")
        logger.info("Activando fallback transparente a SQLite para desarrollo y pruebas...")
        DATABASE_URL = FALLBACK_SQLITE_URL
        return create_engine(FALLBACK_SQLITE_URL, connect_args={"check_same_thread": False})

engine = init_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.orm import Session

logger = logging.getLogger("noti.main")

from app.database import engine, Base, SessionLocal, get_db
from app.routers import auth_routes, team_routes, catalog_routes, schedule_routes, user_routes
from app.seeds import run_seeds

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Crear tablas en PostgreSQL o SQLite
    Base.metadata.create_all(bind=engine)
    # Ejecutar seeds iniciales
    db = SessionLocal()
    try:
        run_seeds(db)
    finally:
        db.close()
    yield

app = FastAPI(
    title="NoTI - Módulo de Gestión de Horarios y Turnos",
    description="Plataforma de planificación colaborativa de horarios y turnos por equipos con validación de cupos y exclusividad del rol SCHEDULER",
    version="1.0.0",
    lifespan=lifespan
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, (StarletteHTTPException, RequestValidationError)):
        raise exc
    logger.exception(f"Unhandled error processing {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": f"Error interno del servidor: {str(exc)}"}
    )

# Servir archivos estáticos y plantillas HTML
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
static_dir = os.path.join(BASE_DIR, "static")
templates_dir = os.path.join(BASE_DIR, "templates")

os.makedirs(static_dir, exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)

# Incluir routers API
app.include_router(auth_routes.router)
app.include_router(user_routes.router)
app.include_router(team_routes.router)
app.include_router(catalog_routes.router)
app.include_router(schedule_routes.router)

@app.get("/login", response_class=HTMLResponse)
async def login_view(request: Request):
    """Vista de Inicio de Sesión"""
    return templates.TemplateResponse(request=request, name="login.html")

@app.get("/", response_class=HTMLResponse)
async def home_view(request: Request):
    """Vista principal: Grilla Semanal y Panel de Turnos"""
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/catalog", response_class=HTMLResponse)
async def catalog_view(request: Request):
    """Vista de Administración del Catálogo de Turnos (HU-02)"""
    return templates.TemplateResponse(request=request, name="catalog.html")

@app.get("/teams", response_class=HTMLResponse)
async def teams_view(request: Request):
    """Vista de Gestión de Equipos y Cupos Diarios (HU-01, HU-03)"""
    return templates.TemplateResponse(request=request, name="teams.html")

@app.get("/users", response_class=HTMLResponse)
async def users_view(request: Request):
    """Vista de Gestión de Usuarios y Accesos"""
    return templates.TemplateResponse(request=request, name="users.html")

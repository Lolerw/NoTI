import os
import tempfile
import concurrent.futures
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    User, Team, TeamMember, ShiftBlockCatalog, RoleEnum, DurationTypeEnum,
    WeeklySchedule, WeeklyScheduleMemberStatus, ShiftAssignment,
    MemberWorkflowStateEnum, PeriodEnum
)
from app.services.schedule_service import ScheduleService
from app.schemas import WeeklyGridResponse, MemberStatusResponse, GridRowResponse, UserResponse
from fastapi import HTTPException

# Base de datos SQLite temporal en archivo para pruebas reales multihilo
temp_db_file = os.path.join(tempfile.gettempdir(), "test_grid_resilience.db")
TEST_DATABASE_URL = f"sqlite:///{temp_db_file}"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionTest = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="module", autouse=True)
def setup_database():
    if os.path.exists(temp_db_file):
        try:
            os.remove(temp_db_file)
        except Exception:
            pass

    Base.metadata.create_all(bind=engine)
    db = SessionTest()

    # 1. Crear equipo de prueba
    team = Team(
        id=1,
        name="Equipo QA Resiliencia",
        rules_config={
            "quotas": {
                "Monday": {"REMOTO": 2},
                "Tuesday": {"REMOTO": 1}
            }
        }
    )
    db.add(team)

    # 2. Crear usuarios y miembros SCHEDULER
    u1 = User(id=10, username="user1", password_hash="hash1", name="User Uno", email="u1@test.com")
    u2 = User(id=11, username="user2", password_hash="hash2", name="User Dos", email="u2@test.com")
    db.add_all([u1, u2])
    db.commit()

    m1 = TeamMember(team_id=1, user_id=10, role=RoleEnum.SCHEDULER.value, participates_in_schedule=True)
    m2 = TeamMember(team_id=1, user_id=11, role=RoleEnum.SCHEDULER.value, participates_in_schedule=True)
    db.add_all([m1, m2])

    # 3. Catálogo
    b1 = ShiftBlockCatalog(id=1, name="REMOTO", color="#3B82F6", duration_type=DurationTypeEnum.FULL_DAY.value, requires_coverage=True)
    b2 = ShiftBlockCatalog(id=2, name="PRESENCIAL", color="#10B981", duration_type=DurationTypeEnum.FULL_DAY.value, requires_coverage=True)
    db.add_all([b1, b2])
    db.commit()
    db.close()

    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    if os.path.exists(temp_db_file):
        try:
            os.remove(temp_db_file)
        except Exception:
            pass


def test_concurrent_get_grid_data_creation():
    """
    Simula múltiples hilos concurrentes accediendo simultáneamente a una semana
    que todavía no existe en la base de datos para verificar que no ocurra
    IntegrityError ni colisión de transacciones.
    """
    target_year = 2026
    target_week = 35

    def fetch_grid():
        session = SessionTest()
        try:
            data = ScheduleService.get_grid_data(session, team_id=1, year=target_year, week_number=target_week)
            validated = WeeklyGridResponse.model_validate(data)
            return validated.schedule.week_number
        finally:
            session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_grid) for _ in range(16)]
        results = [f.result() for f in futures]

    assert all(r == target_week for r in results)


def test_single_aggregated_quotas_calculation():
    """
    Verifica que el cálculo optimizado de cupos diarios con GROUP BY
    calcule con precisión el número de turnos ocupados y disponibles.
    """
    db = SessionTest()
    target_week = 36
    schedule = ScheduleService.get_or_create_weekly_schedule(db, 1, 2026, target_week)
    dates = ScheduleService.get_week_dates(2026, target_week)
    lunes = dates[0]

    # Asignar turno REMOTO a user 10 en estado CONFIRMED
    assign = ShiftAssignment(
        weekly_schedule_id=schedule.id,
        user_id=10,
        date=lunes,
        shift_block_id=1, # REMOTO
        period=PeriodEnum.FULL_DAY.value,
        is_draft=False
    )
    db.add(assign)

    status_10 = db.query(WeeklyScheduleMemberStatus).filter(
        WeeklyScheduleMemberStatus.weekly_schedule_id == schedule.id,
        WeeklyScheduleMemberStatus.user_id == 10
    ).first()
    status_10.workflow_state = MemberWorkflowStateEnum.CONFIRMED.value
    db.commit()

    # Obtener grilla
    grid = ScheduleService.get_grid_data(db, 1, 2026, target_week)
    db.close()

    monday_quota = grid["quotas"][lunes]["quotas"]["REMOTO"]
    assert monday_quota["limit"] == 2
    assert monday_quota["used"] == 1
    assert monday_quota["available"] == 1
    assert monday_quota["is_full"] is False


def test_schema_resilience_with_string_rejection_history():
    """
    Verifica que MemberStatusResponse acepte y parsee automáticamente
    rejection_history cuando llega como string JSON desde la base de datos.
    """
    u = UserResponse(id=1, username="test", name="Test User", email="test@test.com")
    raw_status = {
        "id": 1,
        "weekly_schedule_id": 100,
        "user_id": 1,
        "workflow_state": "DRAFT",
        "rejection_history": '[{"reason": "discrepancia", "timestamp": "2026-11-06T10:00:00"}]',
        "user": u
    }
    validated = MemberStatusResponse.model_validate(raw_status)
    assert isinstance(validated.rejection_history, list)
    assert len(validated.rejection_history) == 1
    assert validated.rejection_history[0]["reason"] == "discrepancia"


def test_get_grid_data_nonexistent_team_raises_404():
    """
    Verifica que al consultar un equipo inexistente se retorne 404 en lugar de error 500.
    """
    db = SessionTest()
    with pytest.raises(HTTPException) as exc_info:
        ScheduleService.get_grid_data(db, team_id=99999, year=2026, week_number=45)
    assert exc_info.value.status_code == 404
    db.close()


def test_rapid_consecutive_week_navigation():
    """
    Simula la navegación rápida entre semanas 44, 45, 46, 47, 48, 49, 50
    verificando que todas retornen respuestas válidas y estructuradas.
    """
    db = SessionTest()
    for w in [44, 45, 46, 47, 48, 49, 50]:
        data = ScheduleService.get_grid_data(db, team_id=1, year=2026, week_number=w)
        validated = WeeklyGridResponse.model_validate(data)
        assert validated.schedule.week_number == w
        assert len(validated.dates) == 7
        assert len(validated.rows) == 2
    db.close()

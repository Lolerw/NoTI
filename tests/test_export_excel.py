import io
import os
import tempfile
import pytest
import openpyxl
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    User, Team, TeamMember, ShiftBlockCatalog, RoleEnum, DurationTypeEnum,
    WeeklySchedule, WeeklyScheduleMemberStatus, ShiftAssignment,
    ScheduleStatusEnum, MemberWorkflowStateEnum, PeriodEnum
)
from app.services.schedule_service import ScheduleService
from fastapi import HTTPException

temp_db_file = os.path.join(tempfile.gettempdir(), "test_export_excel.db")
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

    # 1. Crear equipo
    team = Team(
        id=1,
        name="Equipo Soporte Turnos",
        description="Equipo para pruebas de exportación"
    )
    db.add(team)

    # 2. Crear usuarios y miembros SCHEDULER
    u1 = User(id=1, username="admin_exp", password_hash="h1", name="Admin Exportador", email="admin.exp@test.com")
    u2 = User(id=2, username="colab_exp", password_hash="h2", name="Colaborador Operativo", email="colab.exp@test.com")
    db.add_all([u1, u2])
    db.commit()

    m1 = TeamMember(team_id=1, user_id=1, role=RoleEnum.ADMIN.value, participates_in_schedule=False)
    m2 = TeamMember(team_id=1, user_id=2, role=RoleEnum.SCHEDULER.value, participates_in_schedule=True)
    db.add_all([m1, m2])

    # 3. Bloques
    b1 = ShiftBlockCatalog(id=1, name="REMOTO", color="#3B82F6", duration_type=DurationTypeEnum.FULL_DAY.value, requires_coverage=True)
    b2 = ShiftBlockCatalog(id=2, name="PRESENCIAL", color="#10B981", duration_type=DurationTypeEnum.FULL_DAY.value, requires_coverage=True)
    db.add_all([b1, b2])

    # 4. Semana consolidada con asignaciones
    schedule = WeeklySchedule(id=10, team_id=1, year=2026, week_number=45, status=ScheduleStatusEnum.CONSOLIDATED.value)
    db.add(schedule)

    status_u2 = WeeklyScheduleMemberStatus(
        weekly_schedule_id=10,
        user_id=2,
        workflow_state=MemberWorkflowStateEnum.CONFIRMED.value,
        rejection_history=[]
    )
    db.add(status_u2)

    assign = ShiftAssignment(
        weekly_schedule_id=10,
        user_id=2,
        date="2026-11-02",
        shift_block_id=1, # REMOTO
        period=PeriodEnum.FULL_DAY.value,
        is_draft=False
    )
    db.add(assign)
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


def test_export_consolidated_shifts_excel():
    """
    Verifica que la exportación en formato XLSX genere un archivo Excel válido
    utilizando openpyxl, con la hoja 'Propuesta 1 - Semanal' y datos estructurados.
    """
    db = SessionTest()
    content, media_type, filename = ScheduleService.export_consolidated_shifts(
        db=db,
        team_id=1,
        start_date="2026-11-01",
        end_date="2026-11-30",
        format_type="xlsx"
    )
    db.close()

    assert media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert filename.endswith(".xlsx")
    assert len(content) > 0

    # Cargar el libro Excel generado desde memoria para validar integridad
    wb = openpyxl.load_workbook(io.BytesIO(content))
    assert "Propuesta 1 - Semanal" in wb.sheetnames
    ws = wb["Propuesta 1 - Semanal"]
    assert ws["A1"].value == "Equipo"
    assert ws["B1"].value == "Colaborador"
    assert ws["A4"].value == "Equipo Soporte Turnos"
    assert ws["B4"].value == "Colaborador Operativo"


def test_export_consolidated_shifts_csv():
    """
    Verifica que la exportación en formato CSV genere contenido delimitado válido.
    """
    db = SessionTest()
    content, media_type, filename = ScheduleService.export_consolidated_shifts(
        db=db,
        team_id=1,
        start_date="2026-11-01",
        end_date="2026-11-30",
        format_type="csv"
    )
    db.close()

    assert media_type == "text/csv"
    assert filename.endswith(".csv")
    csv_text = content.decode("utf-8-sig")
    assert "Equipo;Colaborador" in csv_text
    assert "Colaborador Operativo" in csv_text


def test_export_invalid_inputs():
    """
    Verifica que equipos inexistentes o fechas inválidas lancen excepciones HTTP apropiadas.
    """
    db = SessionTest()
    with pytest.raises(HTTPException) as exc_team:
        ScheduleService.export_consolidated_shifts(db=db, team_id=99999, start_date="2026-11-01", end_date="2026-11-30")
    assert exc_team.value.status_code == 404

    with pytest.raises(HTTPException) as exc_date:
        ScheduleService.export_consolidated_shifts(db=db, team_id=1, start_date="fecha-invalida", end_date="2026-11-30")
    assert exc_date.value.status_code == 400
    db.close()

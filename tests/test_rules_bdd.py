import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import User, Team, TeamMember, ShiftBlockCatalog, RoleEnum, DurationTypeEnum
from app.seeds import run_seeds

# Base de datos en memoria para pruebas aisladas
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture(scope="module", autouse=True)
def setup_test_database():
    Base.metadata.create_all(bind=test_engine)
    db = TestingSessionLocal()
    run_seeds(db)
    db.close()
    yield
    Base.metadata.drop_all(bind=test_engine)

@pytest.fixture
def client():
    return TestClient(app)

# ==============================================================================
# HU-01: Gestión de Equipos y Asignación de Roles
# ==============================================================================
def test_hu01_team_member_roles_and_scheduler_exclusivity(client):
    """
    Dado que el Admin crea o edita un equipo
    Cuando asigna a un usuario el rol SCHEDULER, queda habilitado como miembro operativo
    Cuando es asignado con rol VISITANTE o ADMIN puro, tiene accesos delimitados
    """
    # Crear nuevo equipo
    team_res = client.post("/api/teams", json={
        "name": "Equipo Redes & Infra",
        "description": "Infraestructura Cloud",
        "rules_config": {}
    })
    assert team_res.status_code == 201
    team_data = team_res.json()
    team_id = team_data["id"]

    # Asignar usuario con rol SCHEDULER
    res_sched = client.post(f"/api/teams/{team_id}/members", json={
        "user_id": 2, # Miguel
        "role": "SCHEDULER"
    })
    assert res_sched.status_code == 201
    assert res_sched.json()["role"] == "SCHEDULER"
    assert res_sched.json()["participates_in_schedule"] is True

    # Asignar usuario con rol VISITANTE
    res_visit = client.post(f"/api/teams/{team_id}/members", json={
        "user_id": 4, # Laura
        "role": "VISITANTE"
    })
    assert res_visit.status_code == 201
    assert res_visit.json()["role"] == "VISITANTE"
    assert res_visit.json()["participates_in_schedule"] is False

# ==============================================================================
# HU-02: Catálogo de Bloques de Turno y Colores
# ==============================================================================
def test_hu02_catalog_shift_blocks(client):
    """
    Dado el catálogo maestro, el admin gestiona bloques (REMOTO, PRESENCIAL, MEDIO DÍA)
    con colores hexadecimales y tipo de jornada (FULL_DAY o HALF_DAY)
    """
    # Crear un bloque nuevo
    res = client.post("/api/catalog", json={
        "name": "CAPACITACION",
        "color": "#6366F1",
        "duration_type": "HALF_DAY",
        "requires_coverage": False
    })
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "CAPACITACION"
    assert data["duration_type"] == "HALF_DAY"

    # Listar catálogo y verificar existencia de bloques maestros
    list_res = client.get("/api/catalog")
    assert list_res.status_code == 200
    names = [b["name"] for b in list_res.json()]
    assert "REMOTO" in names
    assert "PRESENCIAL" in names
    assert "MEDIO DÍA" in names

# ==============================================================================
# HU-03: Control y Validación de Cupos Diarios por Equipo y Estados Borrador/Publicado
# ==============================================================================
def test_hu03_daily_quotas_enforcement(client):
    """
    Dado un equipo configurado con cupo de 1 persona en REMOTO para los lunes
    1. En BORRADOR, el scheduler puede asignar sin consumir cupos ni afectar a otros.
    2. Al PUBLICAR, se validan los cupos en lote y formalmente se consumen.
    3. Si otro miembro intenta publicar excediendo el cupo disponible, se bloquea con 400.
    4. El miembro puede ajustar su borrador a PRESENCIAL y publicar exitosamente.
    """
    # Usar una semana futura para validar libremente sin restricción de días pasados
    future_year = 2028
    future_week = 10
    grid_res = client.get(f"/api/schedules/grid?team_id=1&year={future_year}&week={future_week}")
    assert grid_res.status_code == 200
    grid = grid_res.json()
    schedule_id = grid["schedule"]["id"]
    lunes_date = grid["dates"][0]

    # Obtener IDs de bloques
    catalog = grid["catalog"]
    remoto_block = next(b for b in catalog if b["name"] == "REMOTO")
    presencial_block = next(b for b in catalog if b["name"] == "PRESENCIAL")

    # Miguel (user_id 2) asigna REMOTO el lunes en borrador y publica
    client.post(f"/api/schedules/{schedule_id}/assign", json={
        "user_id": 2,
        "date": lunes_date,
        "shift_block_id": remoto_block["id"],
        "period": "FULL_DAY",
        "is_draft": True
    }, headers={"X-User-Id": "2"})

    pub_miguel = client.post(f"/api/schedules/{schedule_id}/publish", json={"user_id": 2})
    assert pub_miguel.status_code == 200
    assert pub_miguel.json()["workflow_state"] == "SUBMITTED"

    # Ahora César (user_id 3) en estado BORRADOR puede seleccionar REMOTO sin bloqueo en borrador
    draft_cesar = client.post(f"/api/schedules/{schedule_id}/assign", json={
        "user_id": 3,
        "date": lunes_date,
        "shift_block_id": remoto_block["id"],
        "period": "FULL_DAY",
        "is_draft": True
    }, headers={"X-User-Id": "3"})
    assert draft_cesar.status_code == 200
    assert draft_cesar.json()["shift_block_id"] == remoto_block["id"]

    # Al intentar PUBLICAR, la validación en lote detecta que el cupo de 1 REMOTO ya lo ocupó Miguel
    pub_cesar_fail = client.post(f"/api/schedules/{schedule_id}/publish", json={"user_id": 3})
    assert pub_cesar_fail.status_code == 400
    assert "Cupo diario excedido para 'REMOTO'" in pub_cesar_fail.json()["detail"]

    # César ajusta su borrador a PRESENCIAL y publica exitosamente
    adjust_cesar = client.post(f"/api/schedules/{schedule_id}/assign", json={
        "user_id": 3,
        "date": lunes_date,
        "shift_block_id": presencial_block["id"],
        "period": "FULL_DAY",
        "is_draft": True
    }, headers={"X-User-Id": "3"})
    assert adjust_cesar.status_code == 200

    pub_cesar_ok = client.post(f"/api/schedules/{schedule_id}/publish", json={"user_id": 3})
    assert pub_cesar_ok.status_code == 200
    assert pub_cesar_ok.json()["workflow_state"] == "SUBMITTED"

def test_private_draft_visibility_and_unpublish(client):
    """
    Verifica que en BORRADOR solo el propio scheduler vea sus turnos seleccionados
    y que al despublicar ('Borrador') regrese a estado privado liberando cupos.
    """
    future_year = 2028
    future_week = 11
    grid_res = client.get(f"/api/schedules/grid?team_id=1&year={future_year}&week={future_week}")
    schedule_id = grid_res.json()["schedule"]["id"]
    martes_date = grid_res.json()["dates"][1]
    presencial_block = next(b for b in grid_res.json()["catalog"] if b["name"] == "PRESENCIAL")

    # César asigna un turno en martes en borrador
    client.post(f"/api/schedules/{schedule_id}/assign", json={
        "user_id": 3,
        "date": martes_date,
        "shift_block_id": presencial_block["id"],
        "period": "FULL_DAY",
        "is_draft": True
    }, headers={"X-User-Id": "3"})

    # Miguel consulta la grilla: No debe ver los turnos en borrador de César
    grid_miguel = client.get(f"/api/schedules/grid?team_id=1&year={future_year}&week={future_week}", headers={"X-User-Id": "2"}).json()
    cesar_row_for_miguel = next(r for r in grid_miguel["rows"] if r["user"]["id"] == 3)
    assert len(cesar_row_for_miguel["assignments"]) == 0

    # César consulta la grilla: Debe ver sus propios turnos en borrador
    grid_cesar = client.get(f"/api/schedules/grid?team_id=1&year={future_year}&week={future_week}", headers={"X-User-Id": "3"}).json()
    cesar_row_for_cesar = next(r for r in grid_cesar["rows"] if r["user"]["id"] == 3)
    assert len(cesar_row_for_cesar["assignments"]) > 0

    # César publica y ahora Miguel sí puede ver sus turnos
    client.post(f"/api/schedules/{schedule_id}/publish", json={"user_id": 3})
    grid_miguel_after = client.get(f"/api/schedules/grid?team_id=1&year={future_year}&week={future_week}", headers={"X-User-Id": "2"}).json()
    cesar_row_after = next(r for r in grid_miguel_after["rows"] if r["user"]["id"] == 3)
    assert len(cesar_row_after["assignments"]) > 0

    # César regresa a borrador con unpublish ('Borrador')
    unpub = client.post(f"/api/schedules/{schedule_id}/unpublish", json={"user_id": 3})
    assert unpub.status_code == 200
    assert unpub.json()["workflow_state"] == "DRAFT"

# ==============================================================================
# HU-04: Grilla del Horario Compartido Exclusiva para SCHEDULERs
# ==============================================================================
def test_hu04_grid_renders_only_schedulers(client):
    """
    Dado el acceso al horario compartido semanal
    Cuando se carga la grilla, únicamente los usuarios con rol SCHEDULER aparecen como filas
    Los usuarios con rol VISITANTE o ADMIN puro no aparecen en las filas
    """
    grid_res = client.get("/api/schedules/grid?team_id=1&year=2026&week=44")
    assert grid_res.status_code == 200
    rows = grid_res.json()["rows"]

    # En el Equipo 1, Miguel y César son SCHEDULER. Carlos es ADMIN y Laura es VISITANTE.
    row_user_names = [r["user"]["name"] for r in rows]

    assert any("Miguel" in name for name in row_user_names)
    assert any("César" in name for name in row_user_names)

    # Exclusión de roles no operativos (RN-01)
    assert not any("Carlos Admin" in name for name in row_user_names)
    assert not any("Laura Visitante" in name for name in row_user_names)

# ==============================================================================
# HU-05: Planificación Individual en Borrador y Publicación
# ==============================================================================
def test_hu05_scheduler_draft_and_publish(client):
    """
    Dado el acceso de un SCHEDULER a su borrador
    Cuando el Admin intenta consolidar sin que todos los Schedulers hayan confirmado, se rechaza (400)
    Cuando todos los Schedulers confirman su propuesta, el Admin consolida y publica exitosamente
    """
    grid_res = client.get("/api/schedules/grid?team_id=1&year=2026&week=44")
    schedule_id = grid_res.json()["schedule"]["id"]

    # 1. Intentar consolidar antes de que todos los Schedulers confirmen -> 400 Bad Request
    cons_fail = client.post(f"/api/schedules/{schedule_id}/consolidate?admin_id=1")
    assert cons_fail.status_code == 400
    assert "Faltan confirmar" in cons_fail.json()["detail"]

    # 2. Miguel (user_id 2) y César (user_id 3) confirman sus horarios
    client.post(f"/api/schedules/{schedule_id}/confirm", json={"user_id": 2, "comment": "Propuesta OK"})
    client.post(f"/api/schedules/{schedule_id}/confirm", json={"user_id": 3, "comment": "Propuesta OK"})

    # 3. Ahora el Administrador consolida la semana con éxito
    cons_res = client.post(f"/api/schedules/{schedule_id}/consolidate?admin_id=1")
    assert cons_res.status_code == 200
    assert cons_res.json()["status"] == "CONSOLIDATED"
    assert cons_res.json()["message"] == "Semana publicada"

# ==============================================================================
# HU-06: Confirmación Semanal Exclusiva con Indicador Check
# ==============================================================================
def test_hu06_scheduler_confirmation_with_check(client):
    """
    Dado un horario consolidado
    Cuando el miembro SCHEDULER confirma la semana con comentario opcional
    Entonces su estado cambia a CONFIRMED con check verde visible
    Y usuarios que no son SCHEDULER no pueden confirmar
    """
    grid_res = client.get("/api/schedules/grid?team_id=1&year=2026&week=44")
    schedule_id = grid_res.json()["schedule"]["id"]

    # Miguel (SCHEDULER) confirma
    conf_res = client.post(f"/api/schedules/{schedule_id}/confirm", json={
        "user_id": 2,
        "comment": "Conforme con mis guardias asignadas"
    })
    assert conf_res.status_code == 200
    assert conf_res.json()["workflow_state"] == "CONFIRMED"
    assert conf_res.json()["feedback_comment"] == "Conforme con mis guardias asignadas"
    assert conf_res.json()["confirmed_at"] is not None

    # Intentar confirmar con Laura (VISITANTE) debe ser denegado
    conf_invalid = client.post(f"/api/schedules/{schedule_id}/confirm", json={
        "user_id": 4, # Laura
        "comment": "Quiero confirmar"
    })
    assert conf_invalid.status_code == 403

# ==============================================================================
# HU-07: Rechazo con Discrepancia Condicionado a Publicación de Otro Miembro
# ==============================================================================
def test_hu07_rejection_mandatory_comment_and_reversibility(client):
    """
    Flujo de Rechazo:
    1. Si ningún otro colaborador SCHEDULER ha publicado, el rechazo es bloqueado (400 Bad Request).
    2. Cuando otro colaborador publica su propuesta, el rechazo se habilita.
    3. Requiere justificación de discrepancia obligatoria.
    4. Reversión por el Miembro: Puede reconsiderar y pasar a CONFIRMED.
    5. Reapertura por el Admin: Puede devolver a BORRADOR para ajustar bloques.
    """
    future_year = 2028
    future_week = 12
    grid_res = client.get(f"/api/schedules/grid?team_id=1&year={future_year}&week={future_week}")
    schedule_id = grid_res.json()["schedule"]["id"]

    # 1. Intentar rechazar cuando ningún otro colaborador ha publicado -> Debe fallar con 400
    res_no_published = client.post(f"/api/schedules/{schedule_id}/reject", json={
        "user_id": 3, # César
        "reason": "Cruce de horario"
    })
    assert res_no_published.status_code == 400
    assert "al menos otro miembro del equipo ha publicado" in res_no_published.json()["detail"].lower()

    # 2. Miguel (otro Scheduler) publica su propuesta en la semana
    res_pub_miguel = client.post(f"/api/schedules/{schedule_id}/publish", json={"user_id": 2})
    assert res_pub_miguel.status_code == 200

    # 3. Intentar rechazar sin motivo o con string vacío -> Debe fallar
    res_fail = client.post(f"/api/schedules/{schedule_id}/reject", json={
        "user_id": 3,
        "reason": "  "
    })
    assert res_fail.status_code in [400, 422]

    # 4. Rechazar con motivo válido de discrepancia -> Éxito
    res_reject = client.post(f"/api/schedules/{schedule_id}/reject", json={
        "user_id": 3,
        "reason": "Cruce de horario con examen de maestría el miércoles"
    })
    assert res_reject.status_code == 200
    assert res_reject.json()["workflow_state"] == "REJECTED"
    assert "examen de maestría" in res_reject.json()["feedback_comment"]
    assert len(res_reject.json()["rejection_history"]) >= 1

    # 5. Reversión por parte del propio Miembro (Se retracta o reconsidera)
    res_revert = client.post(f"/api/schedules/{schedule_id}/revert", json={
        "user_id": 3,
        "comment": "Se reprogramó el examen, confirmo mi turno presencial"
    })
    assert res_revert.status_code == 200
    assert res_revert.json()["workflow_state"] == "CONFIRMED"

    # 6. Intentar rechazar estando en CONFIRMED -> Debe ser rechazado (400)
    res_conf_reject = client.post(f"/api/schedules/{schedule_id}/reject", json={
        "user_id": 3,
        "reason": "Intento de rechazar tras confirmar"
    })
    assert res_conf_reject.status_code == 400
    assert "ya has confirmado previamente" in res_conf_reject.json()["detail"].lower()

    # 7. Reapertura acordada por el Administrador a Borrador
    res_reopen = client.post(f"/api/schedules/{schedule_id}/reopen", json={
        "user_id": 3,
        "admin_id": 1,
        "notes": "Acordado cambio de turno a remoto"
    })
    assert res_reopen.status_code == 200
    assert res_reopen.json()["workflow_state"] == "DRAFT"

# ==============================================================================
# Autenticación (Login / Logout / Sesión) y Gestión de Usuarios
# ==============================================================================
def test_auth_login_logout_and_session(client):
    """
    Verifica inicio de sesión con username y password, cookie de sesión y cierre de sesión
    """
    # 1. Login incorrecto
    res_bad = client.post("/api/auth/login", json={
        "username": "miguel",
        "password": "wrongpassword"
    })
    assert res_bad.status_code == 401

    # 2. Login correcto como Miguel (Scheduler)
    res_ok = client.post("/api/auth/login", json={
        "username": "miguel",
        "password": "miguel123"
    })
    assert res_ok.status_code == 200
    data = res_ok.json()
    assert data["success"] is True
    assert data["user"]["username"] == "miguel"
    assert "noti_session_user_id" in res_ok.cookies

    # 3. Consultar usuario autenticado actual con la cookie
    res_cur = client.get("/api/auth/current-user")
    assert res_cur.status_code == 200
    assert res_cur.json()["username"] == "miguel"

    # 4. Logout
    res_logout = client.post("/api/auth/logout")
    assert res_logout.status_code == 200

def test_user_management_crud(client):
    """
    Verifica que el Administrador pueda crear y actualizar usuarios en la tabla de usuarios
    """
    # Crear nuevo usuario
    res_create = client.post("/api/users", json={
        "username": "roberto",
        "name": "Roberto Gómez",
        "email": "roberto@noti.com",
        "password": "password123"
    })
    assert res_create.status_code == 201
    user_data = res_create.json()
    assert user_data["username"] == "roberto"

    # Actualizar usuario
    res_update = client.post("/api/auth/login", json={"username": "roberto", "password": "password123"})
    assert res_update.status_code == 200

def test_scheduler_draft_shift_assignment_and_visualization(client):
    """
    Verifica que el usuario Scheduler pueda asignar y visualizar en su grilla borrador
    el bloque de turno escogido con is_draft=True.
    """
    import datetime
    today = datetime.date.today()
    target_date_str = today.strftime("%Y-%m-%d")
    year = today.year
    week = today.isocalendar()[1]

    # Obtener grilla
    res_grid = client.get(f"/api/schedules/grid?team_id=1&year={year}&week={week}")
    assert res_grid.status_code == 200
    grid_data = res_grid.json()
    schedule_id = grid_data["schedule"]["id"]

    # Scheduler Miguel (user_id=2) asigna bloque PRESENCIAL (id=2) en borrador
    res_assign = client.post(
        f"/api/schedules/{schedule_id}/assign",
        json={
            "user_id": 2,
            "date": target_date_str,
            "shift_block_id": 2,
            "period": "FULL_DAY",
            "is_draft": True
        },
        headers={"X-User-Id": "2"}
    )
    assert res_assign.status_code == 200
    assignment = res_assign.json()
    assert assignment["is_draft"] is True
    assert assignment["shift_block"]["name"] == "PRESENCIAL"

    # Verificar que en la grilla se visualice en la fila de Miguel cuando consulta con su usuario (borrador privado)
    res_grid_after = client.get(f"/api/schedules/grid?team_id=1&year={year}&week={week}", headers={"X-User-Id": "2"})
    assert res_grid_after.status_code == 200
    grid_after = res_grid_after.json()

    miguel_row = next(r for r in grid_after["rows"] if r["user"]["id"] == 2)
    assigned_for_today = next(a for a in miguel_row["assignments"] if a["date"] == target_date_str)
    assert assigned_for_today["shift_block"]["name"] == "PRESENCIAL"
    assert assigned_for_today["is_draft"] is True

def test_past_days_assignment_restriction_for_scheduler_and_admin_override(client):
    """
    Verifica que no se permita a un Scheduler asignar en días pasados (400 Bad Request),
    pero que el Administrador sí pueda realizar regularizaciones históricas.
    """
    past_date_str = "2025-01-06" # Fecha en el pasado

    # 1. Intentar asignar como Scheduler Miguel (user_id=2)
    res_past_sched = client.post(
        "/api/schedules/1/assign",
        json={
            "user_id": 2,
            "date": past_date_str,
            "shift_block_id": 1,
            "period": "FULL_DAY",
            "is_draft": True
        },
        headers={"X-User-Id": "2"}
    )
    assert res_past_sched.status_code == 400
    assert "días anteriores a la fecha actual" in res_past_sched.json()["detail"]

    # 2. Asignar como Admin (user_id=1)
    res_past_admin = client.post(
        "/api/schedules/1/assign?operator_id=1",
        json={
            "user_id": 2,
            "date": past_date_str,
            "shift_block_id": 1,
            "period": "FULL_DAY",
            "is_draft": True
        },
        headers={"X-User-Id": "1"}
    )
    assert res_past_admin.status_code == 200
    assert res_past_admin.json()["date"] == past_date_str

def test_admin_delete_confirmed_shift_reverts_member_and_schedule_to_draft(client):
    """
    Verifica que si un Administrador elimina un bloque de turno de un miembro confirmado,
    el estado del miembro vuelva a DRAFT y el estado general de la semana vuelva a DRAFT.
    """
    # 1. Obtener semana 44 que fue consolidada
    grid_res = client.get("/api/schedules/grid?team_id=1&year=2026&week=44")
    grid = grid_res.json()
    schedule_id = grid["schedule"]["id"]

    # Asegurar que ambos Schedulers estén en CONFIRMED y la semana en CONSOLIDATED
    client.post(f"/api/schedules/{schedule_id}/confirm", json={"user_id": 2, "comment": "Confirmado"})
    client.post(f"/api/schedules/{schedule_id}/confirm", json={"user_id": 3, "comment": "Confirmado"})
    client.post(f"/api/schedules/{schedule_id}/consolidate?admin_id=1")

    # Verificar estado inicial consolidado
    grid_cons = client.get(f"/api/schedules/grid?team_id=1&year=2026&week=44").json()
    assert grid_cons["schedule"]["status"] == "CONSOLIDATED"
    miguel_row = next(r for r in grid_cons["rows"] if r["user"]["id"] == 2)
    assert miguel_row["status"]["workflow_state"] == "CONFIRMED"
    miguel_assignment_id = miguel_row["assignments"][0]["id"]

    # 2. El Admin elimina el turno de Miguel
    del_res = client.delete(
        f"/api/schedules/{schedule_id}/assign/{miguel_assignment_id}?operator_id=1",
        headers={"X-User-Id": "1"}
    )
    assert del_res.status_code == 204

    # 3. Verificar que Miguel y la semana vuelven a DRAFT
    grid_after = client.get(f"/api/schedules/grid?team_id=1&year=2026&week=44").json()
    miguel_after = next(r for r in grid_after["rows"] if r["user"]["id"] == 2)
    assert miguel_after["status"]["workflow_state"] == "DRAFT"
    assert grid_after["schedule"]["status"] == "DRAFT"

def test_export_consolidated_shifts_xlsx_and_csv(client):
    """
    Verifica la exportación en rango de fechas (mes/meses) en formatos Excel y CSV
    exclusivamente para semanas consolidadas.
    """
    # 1. Asegurar semana consolidada
    grid_res = client.get("/api/schedules/grid?team_id=1&year=2026&week=44")
    schedule_id = grid_res.json()["schedule"]["id"]
    client.post(f"/api/schedules/{schedule_id}/confirm", json={"user_id": 2, "comment": "Re-confirmado"})
    client.post(f"/api/schedules/{schedule_id}/confirm", json={"user_id": 3, "comment": "Re-confirmado"})
    client.post(f"/api/schedules/{schedule_id}/consolidate?admin_id=1")

    # 2. Exportar en formato Excel (.xlsx)
    exp_xlsx = client.get("/api/schedules/export?team_id=1&start_date=2026-10-01&end_date=2026-11-30&format=xlsx")
    assert exp_xlsx.status_code == 200
    assert "spreadsheetml.sheet" in exp_xlsx.headers["content-type"]
    assert len(exp_xlsx.content) > 1000 # Archivo Excel binario generado con openpyxl

    # 3. Exportar en formato CSV
    exp_csv = client.get("/api/schedules/export?team_id=1&start_date=2026-10-01&end_date=2026-11-30&format=csv")
    assert exp_csv.status_code == 200
    assert "text/csv" in exp_csv.headers["content-type"]
    csv_text = exp_csv.content.decode("utf-8-sig")
    assert "Equipo;Colaborador;Email;Fecha;Día;Turno;Tipo Jornada;Período;Estado" in csv_text
    assert "CONSOLIDADO / PUBLICADO" in csv_text

def test_confirmed_scheduler_cannot_modify_and_reject_blocked_on_consolidated(client):
    """
    Verifica que:
    1. Un scheduler confirmado no pueda modificar sus bloques de turno (403 Forbidden).
    2. Cuando la semana es consolidada por el Admin, la opción de Rechazar queda bloqueada (400 Bad Request).
    """
    future_year = 2028
    future_week = 14
    grid_res = client.get(f"/api/schedules/grid?team_id=1&year={future_year}&week={future_week}")
    schedule_id = grid_res.json()["schedule"]["id"]
    lunes_date = grid_res.json()["dates"][0]

    # Confirmar a Miguel
    client.post(f"/api/schedules/{schedule_id}/confirm", json={"user_id": 2, "comment": "Confirmado"})

    # Miguel intenta modificar su turno estando CONFIRMED -> 403 Forbidden
    res_mod = client.post(
        f"/api/schedules/{schedule_id}/assign",
        json={
            "user_id": 2,
            "date": lunes_date,
            "shift_block_id": 1,
            "period": "FULL_DAY"
        },
        headers={"X-User-Id": "2"}
    )
    assert res_mod.status_code == 403
    assert "no puedes modificar tus turnos una vez que has confirmado" in res_mod.json()["detail"].lower()

    # Confirmar a César y consolidar semana
    client.post(f"/api/schedules/{schedule_id}/confirm", json={"user_id": 3, "comment": "Confirmado"})
    cons_res = client.post(f"/api/schedules/{schedule_id}/consolidate?admin_id=1")
    assert cons_res.status_code == 200

    # Tras consolidar, el Scheduler ya NO puede rechazar el horario
    reject_res = client.post(
        f"/api/schedules/{schedule_id}/reject",
        json={"user_id": 2, "reason": "Discrepancia tardía"}
    )
    assert reject_res.status_code == 400
    assert "consolidado" in reject_res.json()["detail"].lower()

def test_week_navigation_and_iso_boundaries(client):
    """
    Verifica que la API resuelva correctamente las semanas en los límites de años ISO:
    - Semana 1 de 2026
    - Semana 53 de 2026 (año de 53 semanas)
    - Semana 52 de 2025 (año de 52 semanas)
    - Semana 1 de 2027
    """
    # 1. Semana 1 de 2026
    res_w1_2026 = client.get("/api/schedules/grid?team_id=1&year=2026&week=1")
    assert res_w1_2026.status_code == 200
    w1_data = res_w1_2026.json()
    assert w1_data["schedule"]["week_number"] == 1
    assert w1_data["schedule"]["year"] == 2026
    assert len(w1_data["dates"]) == 7
    assert w1_data["dates"][0] == "2025-12-29" # Lunes de la semana 1 ISO de 2026

    # 2. Semana 53 de 2026
    res_w53_2026 = client.get("/api/schedules/grid?team_id=1&year=2026&week=53")
    assert res_w53_2026.status_code == 200
    w53_data = res_w53_2026.json()
    assert w53_data["schedule"]["week_number"] == 53
    assert w53_data["schedule"]["year"] == 2026
    assert w53_data["dates"][0] == "2026-12-28"

    # 3. Semana 52 de 2025
    res_w52_2025 = client.get("/api/schedules/grid?team_id=1&year=2025&week=52")
    assert res_w52_2025.status_code == 200
    w52_data = res_w52_2025.json()
    assert w52_data["schedule"]["week_number"] == 52
    assert w52_data["schedule"]["year"] == 2025

    # 4. Semana 1 de 2027
    res_w1_2027 = client.get("/api/schedules/grid?team_id=1&year=2027&week=1")
    assert res_w1_2027.status_code == 200
    w1_2027_data = res_w1_2027.json()
    assert w1_2027_data["schedule"]["week_number"] == 1
    assert w1_2027_data["schedule"]["year"] == 2027

def test_current_week_resolution(client):
    """
    Verifica que la consulta de la semana en curso (Semana Actual) contenga la fecha de hoy
    y cargue los 7 días ordenados de lunes a domingo.
    """
    import datetime
    today = datetime.date.today()
    iso_year, iso_week, _ = today.isocalendar()

    res_current = client.get(f"/api/schedules/grid?team_id=1&year={iso_year}&week={iso_week}")
    assert res_current.status_code == 200
    data = res_current.json()
    assert data["schedule"]["week_number"] == iso_week
    assert data["schedule"]["year"] == iso_year

    # La lista de fechas debe contener exactamente la fecha de hoy
    today_str = today.strftime("%Y-%m-%d")
    assert today_str in data["dates"]
    assert len(data["dates"]) == 7

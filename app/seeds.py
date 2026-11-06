import os
from sqlalchemy.orm import Session
from app.models import (
    User, Team, TeamMember, ShiftBlockCatalog, WeeklySchedule,
    WeeklyScheduleMemberStatus, ShiftAssignment,
    RoleEnum, DurationTypeEnum, ScheduleStatusEnum, MemberWorkflowStateEnum, PeriodEnum
)
from app.services.schedule_service import ScheduleService
from app.utils.security import hash_password

def run_seeds(db: Session):
    app_env = os.getenv("APP_ENV", "development").lower()
    seed_demo = os.getenv("SEED_DEMO_DATA", "true").lower() == "true" and app_env != "production"

    # =========================================================================
    # 1. Catálogo Maestro de Bloques (HU-02) - Siempre se asegura en cualquier entorno
    # =========================================================================
    catalog_data = [
        {"name": "REMOTO", "color": "#3B82F6", "duration_type": DurationTypeEnum.FULL_DAY.value, "requires_coverage": True},
        {"name": "PRESENCIAL", "color": "#10B981", "duration_type": DurationTypeEnum.FULL_DAY.value, "requires_coverage": True},
        {"name": "MEDIO DÍA", "color": "#F59E0B", "duration_type": DurationTypeEnum.HALF_DAY.value, "requires_coverage": True},
        {"name": "PERMISO", "color": "#8B5CF6", "duration_type": DurationTypeEnum.HALF_DAY.value, "requires_coverage": False},
        {"name": "DÍA LIBRE", "color": "#6B7280", "duration_type": DurationTypeEnum.FULL_DAY.value, "requires_coverage": False},
    ]

    block_map = {}
    for c in catalog_data:
        block = db.query(ShiftBlockCatalog).filter(ShiftBlockCatalog.name == c["name"]).first()
        if not block:
            block = ShiftBlockCatalog(
                name=c["name"],
                color=c["color"],
                duration_type=c["duration_type"],
                requires_coverage=c["requires_coverage"]
            )
            db.add(block)
            db.commit()
            db.refresh(block)
        block_map[c["name"]] = block

    # =========================================================================
    # 2. Modo Producción: Sembrar únicamente Administrador inicial
    # =========================================================================
    if not seed_demo:
        admin_email = os.getenv("INITIAL_ADMIN_EMAIL", "admin@noti.com")
        admin_username = os.getenv("INITIAL_ADMIN_USER", "admin")
        admin_password = os.getenv("INITIAL_ADMIN_PASSWORD", "Admin123!")
        admin_name = os.getenv("INITIAL_ADMIN_NAME", "Administrador Principal")

        existing_admin = db.query(User).filter(
            (User.email == admin_email) | (User.username == admin_username)
        ).first()

        if not existing_admin:
            admin_user = User(
                username=admin_username,
                password_hash=hash_password(admin_password),
                name=admin_name,
                email=admin_email,
                avatar_url="https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?w=120&auto=format&fit=crop&q=80"
            )
            db.add(admin_user)
            db.commit()
            db.refresh(admin_user)
            print(f"[PROD] Administrador inicial creado: {admin_username} ({admin_email})")
        else:
            print(f"[PROD] Administrador ya existente ({existing_admin.username}).")

        print("Seeds de producción (catálogo maestro + admin inicial) completados.")
        return

    # =========================================================================
    # 3. Modo Desarrollo: Sembrar usuarios de prueba, equipo y horarios demo
    # =========================================================================
    users_data = [
        {"id": 1, "username": "admin", "password": "admin123", "name": "Carlos Admin", "email": "carlos.admin@noti.com", "avatar_url": "https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?w=120&auto=format&fit=crop&q=80"},
        {"id": 2, "username": "miguel", "password": "miguel123", "name": "Miguel S. (Scheduler)", "email": "miguel.scheduler@noti.com", "avatar_url": "https://images.unsplash.com/photo-1535713875002-d1d0cf377fde?w=120&auto=format&fit=crop&q=80"},
        {"id": 3, "username": "cesar", "password": "cesar123", "name": "César R. (Scheduler)", "email": "cesar.scheduler@noti.com", "avatar_url": "https://images.unsplash.com/photo-1570295999919-56ceb5ecca61?w=120&auto=format&fit=crop&q=80"},
        {"id": 4, "username": "laura", "password": "laura123", "name": "Laura Visitante", "email": "laura.visitor@noti.com", "avatar_url": "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=120&auto=format&fit=crop&q=80"},
    ]

    user_map = {}
    for u in users_data:
        user = db.query(User).filter((User.email == u["email"]) | (User.username == u["username"])).first()
        if not user:
            user = User(
                id=u["id"],
                username=u["username"],
                password_hash=hash_password(u["password"]),
                name=u["name"],
                email=u["email"],
                avatar_url=u["avatar_url"]
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            if not getattr(user, 'username', None) or not getattr(user, 'password_hash', None):
                user.username = u["username"]
                user.password_hash = hash_password(u["password"])
                db.commit()
                db.refresh(user)
        user_map[u["name"]] = user

    team = db.query(Team).filter(Team.name == "Equipo TI / Soporte").first()
    if not team:
        team = Team(
            name="Equipo TI / Soporte",
            description="Equipo de guardia y atención de incidentes para NoTI",
            rules_config={
                "quotas": {
                    "Monday": {"REMOTO": 1},
                    "Tuesday": {"REMOTO": 1},
                    "Wednesday": {"REMOTO": 2},
                    "Thursday": {"REMOTO": 2},
                    "Friday": {"REMOTO": 1}
                }
            }
        )
        db.add(team)
        db.commit()
        db.refresh(team)

    team_memberships = [
        (user_map["Carlos Admin"].id, RoleEnum.ADMIN.value, False),
        (user_map["Miguel S. (Scheduler)"].id, RoleEnum.SCHEDULER.value, True),
        (user_map["César R. (Scheduler)"].id, RoleEnum.SCHEDULER.value, True),
        (user_map["Laura Visitante"].id, RoleEnum.VISITANTE.value, False)
    ]

    for uid, role, participates in team_memberships:
        member = db.query(TeamMember).filter(
            TeamMember.team_id == team.id,
            TeamMember.user_id == uid
        ).first()
        if not member:
            member = TeamMember(
                team_id=team.id,
                user_id=uid,
                role=role,
                participates_in_schedule=participates
            )
            db.add(member)
    db.commit()

    schedule = ScheduleService.get_or_create_weekly_schedule(db, team.id, 2026, 44)
    dates = ScheduleService.get_week_dates(2026, 44)

    lunes_fecha = dates[0]
    existing_assignment = db.query(ShiftAssignment).filter(
        ShiftAssignment.weekly_schedule_id == schedule.id,
        ShiftAssignment.user_id == user_map["Miguel S. (Scheduler)"].id,
        ShiftAssignment.date == lunes_fecha
    ).first()

    if not existing_assignment:
        ScheduleService.assign_shift(
            db=db,
            schedule_id=schedule.id,
            user_id=user_map["Miguel S. (Scheduler)"].id,
            date_str=lunes_fecha,
            shift_block_id=block_map["REMOTO"].id,
            period=PeriodEnum.FULL_DAY.value,
            is_draft=True,
            operator_id=user_map["Carlos Admin"].id
        )

    existing_cesar_lunes = db.query(ShiftAssignment).filter(
        ShiftAssignment.weekly_schedule_id == schedule.id,
        ShiftAssignment.user_id == user_map["César R. (Scheduler)"].id,
        ShiftAssignment.date == lunes_fecha
    ).first()
    if not existing_cesar_lunes:
        ScheduleService.assign_shift(
            db=db,
            schedule_id=schedule.id,
            user_id=user_map["César R. (Scheduler)"].id,
            date_str=lunes_fecha,
            shift_block_id=block_map["PRESENCIAL"].id,
            period=PeriodEnum.FULL_DAY.value,
            is_draft=True,
            operator_id=user_map["Carlos Admin"].id
        )

    db.commit()
    print("Seeds de desarrollo ejecutados satisfactoriamente.")

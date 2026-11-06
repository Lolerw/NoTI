from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Team, TeamMember, User, RoleEnum
from app.schemas import TeamCreate, TeamUpdate, TeamResponse, TeamMemberCreate, TeamMemberResponse

router = APIRouter(prefix="/api/teams", tags=["Gestión de Equipos y Roles (HU-01)"])

@router.get("", response_model=List[TeamResponse])
def get_teams(db: Session = Depends(get_db)):
    """Lista todos los equipos de la organización."""
    return db.query(Team).all()

@router.get("/{team_id}", response_model=TeamResponse)
def get_team_by_id(team_id: int, db: Session = Depends(get_db)):
    """Obtiene el detalle de un equipo específico con sus miembros y reglas."""
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Equipo no encontrado")
    return team

@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
def create_team(team_in: TeamCreate, db: Session = Depends(get_db)):
    """Crea un nuevo equipo y opcionalmente asigna sus miembros iniciales con sus roles (HU-01)."""
    team = Team(
        name=team_in.name,
        description=team_in.description,
        rules_config=team_in.rules_config or {}
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    if team_in.initial_members:
        for m in team_in.initial_members:
            role_str = m.role.upper()
            if role_str in [RoleEnum.ADMIN.value, RoleEnum.SCHEDULER.value, RoleEnum.VISITANTE.value]:
                member = TeamMember(
                    team_id=team.id,
                    user_id=m.user_id,
                    role=role_str,
                    participates_in_schedule=(role_str == RoleEnum.SCHEDULER.value)
                )
                db.add(member)
        db.commit()
        db.refresh(team)

    return team

@router.put("/{team_id}/rules")
def update_team_rules(team_id: int, rules_config: Dict[str, Any], db: Session = Depends(get_db)):
    """Configura las reglas de topes o cupos diarios por bloque para el equipo (HU-03)."""
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Equipo no encontrado")

    team.rules_config = rules_config
    db.commit()
    db.refresh(team)
    return {"success": True, "rules_config": team.rules_config}

@router.post("/{team_id}/members", response_model=TeamMemberResponse, status_code=status.HTTP_201_CREATED)
def add_team_member(team_id: int, member_in: TeamMemberCreate, db: Session = Depends(get_db)):
    """Asigna un usuario al equipo con rol ADMIN, SCHEDULER o VISITANTE (HU-01)."""
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Equipo no encontrado")

    user = db.query(User).filter(User.id == member_in.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    role_str = member_in.role.upper()
    if role_str not in [RoleEnum.ADMIN.value, RoleEnum.SCHEDULER.value, RoleEnum.VISITANTE.value]:
        raise HTTPException(status_code=400, detail="Rol inválido. Debe ser ADMIN, SCHEDULER o VISITANTE")

    existing = db.query(TeamMember).filter(
        TeamMember.team_id == team_id,
        TeamMember.user_id == member_in.user_id
    ).first()

    if existing:
        existing.role = role_str
        existing.participates_in_schedule = (role_str == RoleEnum.SCHEDULER.value)
        db.commit()
        db.refresh(existing)
        return existing

    member = TeamMember(
        team_id=team_id,
        user_id=member_in.user_id,
        role=role_str,
        participates_in_schedule=(role_str == RoleEnum.SCHEDULER.value)
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member

@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_team_member(team_id: int, user_id: int, db: Session = Depends(get_db)):
    """Remueve un miembro del equipo."""
    member = db.query(TeamMember).filter(
        TeamMember.team_id == team_id,
        TeamMember.user_id == user_id
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="Membresía no encontrada")

    db.delete(member)
    db.commit()
    return None

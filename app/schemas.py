import json
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict, field_validator

# --- Autenticación y Usuarios ---
class LoginRequest(BaseModel):
    username: str
    password: str

class UserBase(BaseModel):
    username: str
    name: str
    email: str
    avatar_url: Optional[str] = None

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    password: Optional[str] = None

class UserResponse(UserBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

# --- Miembros de Equipo ---
class TeamMemberCreate(BaseModel):
    user_id: int
    role: str = "SCHEDULER" # ADMIN, SCHEDULER, VISITANTE
    participates_in_schedule: bool = True

class TeamMemberResponse(BaseModel):
    id: int
    team_id: int
    user_id: int
    role: str
    participates_in_schedule: bool
    user: UserResponse
    model_config = ConfigDict(from_attributes=True)

# --- Equipos ---
class TeamBase(BaseModel):
    name: str
    description: Optional[str] = None
    rules_config: Dict[str, Any] = Field(default_factory=dict)

class TeamCreate(TeamBase):
    initial_members: Optional[List[TeamMemberCreate]] = []

class TeamUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    rules_config: Optional[Dict[str, Any]] = None

class TeamResponse(TeamBase):
    id: int
    members: List[TeamMemberResponse] = []
    model_config = ConfigDict(from_attributes=True)

# --- Catálogo de Turnos ---
class ShiftBlockBase(BaseModel):
    name: str
    color: str = "#3B82F6"
    duration_type: str = "FULL_DAY" # FULL_DAY, HALF_DAY
    requires_coverage: bool = True

class ShiftBlockCreate(ShiftBlockBase):
    pass

class ShiftBlockUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    duration_type: Optional[str] = None
    requires_coverage: Optional[bool] = None

class ShiftBlockResponse(ShiftBlockBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

# --- Asignaciones de Turno ---
class ShiftAssignmentCreate(BaseModel):
    user_id: int
    date: str # YYYY-MM-DD
    shift_block_id: int
    period: str = "FULL_DAY" # FULL_DAY, FIRST_HALF, SECOND_HALF
    is_draft: bool = True

class ShiftAssignmentResponse(BaseModel):
    id: int
    weekly_schedule_id: int
    user_id: int
    date: str
    shift_block_id: int
    period: str
    is_draft: bool
    shift_block: ShiftBlockResponse
    model_config = ConfigDict(from_attributes=True)

# --- Estado de Flujo del Miembro ---
class MemberStatusResponse(BaseModel):
    id: int
    weekly_schedule_id: int
    user_id: int
    workflow_state: str # DRAFT, SUBMITTED, CONFIRMED, REJECTED
    feedback_comment: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    rejection_history: Optional[List[Dict[str, Any]]] = None
    user: Optional[UserResponse] = None
    model_config = ConfigDict(from_attributes=True)

    @field_validator("rejection_history", mode="before")
    @classmethod
    def parse_rejection_history(cls, v: Any) -> Optional[List[Dict[str, Any]]]:
        if v is None:
            return []
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else []
            except Exception:
                return []
        return v

# --- Solicitudes de Workflow ---
class PublishProposalRequest(BaseModel):
    user_id: int

class ConfirmScheduleRequest(BaseModel):
    user_id: int
    comment: Optional[str] = None

class RejectScheduleRequest(BaseModel):
    user_id: int
    reason: str = Field(..., min_length=3, description="Comentario obligatorio de discrepancia")

class RevertScheduleRequest(BaseModel):
    user_id: int
    comment: Optional[str] = "Reconsideración de horario y conformidad registrada"

class ReopenScheduleRequest(BaseModel):
    user_id: int
    admin_id: int
    notes: Optional[str] = None

# --- Horario Semanal y Grilla ---
class WeeklyScheduleResponse(BaseModel):
    id: int
    team_id: int
    year: int
    week_number: int
    status: str
    model_config = ConfigDict(from_attributes=True)

class GridRowResponse(BaseModel):
    user: UserResponse
    role: str
    status: Optional[MemberStatusResponse] = None
    assignments: List[ShiftAssignmentResponse] = []

class GridDailyQuotaInfo(BaseModel):
    date: str
    day_name: str
    quotas: Dict[str, Dict[str, int]] # Ej: {"REMOTO": {"limit": 1, "used": 1, "available": 0}}

class WeeklyGridResponse(BaseModel):
    schedule: WeeklyScheduleResponse
    team: TeamResponse
    dates: List[str] # 7 días de la semana (Lunes a Domingo)
    quotas: Dict[str, Any]
    rows: List[GridRowResponse] # Únicamente miembros SCHEDULER
    catalog: List[ShiftBlockResponse]

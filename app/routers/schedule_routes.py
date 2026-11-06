import datetime
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.schedule_service import ScheduleService
from app.models import (
    WeeklySchedule, ShiftAssignment, User, TeamMember, RoleEnum,
    WeeklyScheduleMemberStatus, MemberWorkflowStateEnum, ScheduleStatusEnum
)
from app.schemas import (
    WeeklyGridResponse, ShiftAssignmentCreate, ShiftAssignmentResponse,
    PublishProposalRequest, ConfirmScheduleRequest, RejectScheduleRequest,
    RevertScheduleRequest, ReopenScheduleRequest, MemberStatusResponse
)

router = APIRouter(prefix="/api/schedules", tags=["Horarios Semanales y Grilla Operativa"])

@router.get("/grid", response_model=WeeklyGridResponse)
def get_weekly_grid(
    team_id: int = Query(..., description="ID del equipo"),
    year: int = Query(2026, description="Año"),
    week: int = Query(44, description="Número de semana ISO"),
    request: Request = None,
    viewer_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Retorna la grilla del horario semanal exclusivo para miembros SCHEDULER (HU-04),
    junto al estado de confirmación (HU-06, HU-07) y cupos diarios en vivo (HU-03).
    En estado BORRADOR, las asignaciones solo son visibles para el propio Scheduler dueño del borrador.
    """
    effective_viewer_id = viewer_id
    if not effective_viewer_id and request:
        cookie_uid = request.cookies.get("noti_session_user_id") or request.headers.get("X-User-Id")
        if cookie_uid:
            try:
                effective_viewer_id = int(cookie_uid)
            except ValueError:
                pass

    grid_data = ScheduleService.get_grid_data(db, team_id, year, week, current_user_id=effective_viewer_id)
    return grid_data

@router.post("/{schedule_id}/assign", response_model=ShiftAssignmentResponse)
def assign_shift_to_member(
    schedule_id: int,
    assignment_in: ShiftAssignmentCreate,
    request: Request,
    operator_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Asigna un bloque de turno a un miembro SCHEDULER para una fecha específica,
    validando topes de cupos diarios por equipo (HU-03) y restricción de días pasados.
    """
    effective_op_id = operator_id
    if not effective_op_id:
        cookie_user_id = request.cookies.get("noti_session_user_id") or request.headers.get("X-User-Id")
        if cookie_user_id:
            try:
                effective_op_id = int(cookie_user_id)
            except ValueError:
                pass

    assignment = ScheduleService.assign_shift(
        db=db,
        schedule_id=schedule_id,
        user_id=assignment_in.user_id,
        date_str=assignment_in.date,
        shift_block_id=assignment_in.shift_block_id,
        period=assignment_in.period,
        is_draft=assignment_in.is_draft,
        operator_id=effective_op_id
    )
    return assignment

@router.delete("/{schedule_id}/assign/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shift_assignment(
    schedule_id: int,
    assignment_id: int,
    request: Request,
    operator_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    """Elimina una asignación de turno, restringiendo días pasados para no administradores."""
    assignment = db.query(ShiftAssignment).filter(
        ShiftAssignment.id == assignment_id,
        ShiftAssignment.weekly_schedule_id == schedule_id
    ).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")

    # Si la fecha es pasada, verificar que el operador sea ADMIN
    target_date = datetime.datetime.strptime(assignment.date, "%Y-%m-%d").date()
    today = datetime.date.today()
    if target_date < today:
        effective_op_id = operator_id
        if not effective_op_id:
            cookie_user_id = request.cookies.get("noti_session_user_id") or request.headers.get("X-User-Id")
            if cookie_user_id:
                try:
                    effective_op_id = int(cookie_user_id)
                except ValueError:
                    pass

        is_admin = False
        if effective_op_id:
            op_user = db.query(User).filter(User.id == effective_op_id).first()
            if op_user and op_user.username == "admin":
                is_admin = True
            else:
                schedule = db.query(WeeklySchedule).filter(WeeklySchedule.id == schedule_id).first()
                if schedule:
                    op_member = db.query(TeamMember).filter(
                        TeamMember.team_id == schedule.team_id,
                        TeamMember.user_id == effective_op_id
                    ).first()
                    if op_member and op_member.role == RoleEnum.ADMIN.value:
                        is_admin = True

        if not is_admin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No está permitido eliminar turnos en días anteriores a la fecha actual."
            )

    # Si el miembro dueño del turno estaba confirmado, devolver su estado y la semana a DRAFT
    status_entry = db.query(WeeklyScheduleMemberStatus).filter(
        WeeklyScheduleMemberStatus.weekly_schedule_id == schedule_id,
        WeeklyScheduleMemberStatus.user_id == assignment.user_id
    ).first()
    if status_entry and status_entry.workflow_state == MemberWorkflowStateEnum.CONFIRMED.value:
        status_entry.workflow_state = MemberWorkflowStateEnum.DRAFT.value
        status_entry.feedback_comment = "Turno modificado por Administrador; devuelto a borrador para re-confirmación"
        schedule = db.query(WeeklySchedule).filter(WeeklySchedule.id == schedule_id).first()
        if schedule and schedule.status == ScheduleStatusEnum.CONSOLIDATED.value:
            schedule.status = ScheduleStatusEnum.DRAFT.value

    db.delete(assignment)
    db.commit()
    return None

@router.post("/{schedule_id}/publish", response_model=MemberStatusResponse)
def publish_individual_proposal(
    schedule_id: int,
    req: PublishProposalRequest,
    db: Session = Depends(get_db)
):
    """
    El miembro SCHEDULER publica su propuesta individual desde borrador a SUBMITTED (HU-05).
    """
    status_entry = ScheduleService.publish_proposal(db, schedule_id, req.user_id)
    return status_entry

@router.post("/{schedule_id}/unpublish", response_model=MemberStatusResponse)
def unpublish_individual_proposal(
    schedule_id: int,
    req: PublishProposalRequest,
    db: Session = Depends(get_db)
):
    """
    El miembro SCHEDULER regresa su propuesta de PUBLICADO a BORRADOR para poder ajustar turnos.
    """
    status_entry = ScheduleService.unpublish_to_draft(db, schedule_id, req.user_id)
    return status_entry

@router.post("/{schedule_id}/consolidate")
def consolidate_weekly_schedule(
    schedule_id: int,
    admin_id: int = Query(..., description="ID del usuario administrador"),
    db: Session = Depends(get_db)
):
    """
    El Administrador consolida el horario semanal del equipo cuando el 100% de los SCHEDULER han confirmado.
    """
    schedule = ScheduleService.consolidate_schedule(db, schedule_id, admin_id)
    return {"success": True, "status": schedule.status, "message": "Semana publicada"}

@router.get("/export")
def export_schedule_range(
    team_id: int = Query(..., description="ID del equipo"),
    start_date: str = Query(..., description="Fecha inicio YYYY-MM-DD"),
    end_date: str = Query(..., description="Fecha fin YYYY-MM-DD"),
    format: str = Query("xlsx", description="Formato: xlsx o csv"),
    db: Session = Depends(get_db)
):
    """
    Exporta en Excel (.xlsx) o CSV los turnos dentro de un rango de fechas (mes/meses)
    pertenecientes a semanas consolidadas y publicadas por el Administrador.
    """
    content, media_type, filename = ScheduleService.export_consolidated_shifts(
        db=db,
        team_id=team_id,
        start_date=start_date,
        end_date=end_date,
        format_type=format.lower()
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@router.post("/{schedule_id}/confirm", response_model=MemberStatusResponse)
def confirm_weekly_schedule(
    schedule_id: int,
    req: ConfirmScheduleRequest,
    db: Session = Depends(get_db)
):
    """
    El miembro SCHEDULER confirma formalmente su horario consolidado con check verde visible (HU-06).
    """
    status_entry = ScheduleService.confirm_schedule(db, schedule_id, req.user_id, req.comment)
    return status_entry

@router.post("/{schedule_id}/reject", response_model=MemberStatusResponse)
def reject_weekly_schedule(
    schedule_id: int,
    req: RejectScheduleRequest,
    db: Session = Depends(get_db)
):
    """
    El miembro SCHEDULER rechaza el horario semanal con comentario de discrepancia obligatorio (HU-07).
    """
    status_entry = ScheduleService.reject_schedule(db, schedule_id, req.user_id, req.reason)
    return status_entry

@router.post("/{schedule_id}/revert", response_model=MemberStatusResponse)
def revert_rejection(
    schedule_id: int,
    req: RevertScheduleRequest,
    db: Session = Depends(get_db)
):
    """
    El miembro SCHEDULER reconsidera y revierte su rechazo a CONFIRMADO (HU-07).
    """
    status_entry = ScheduleService.revert_rejection_to_confirmed(db, schedule_id, req.user_id, req.comment)
    return status_entry

@router.post("/{schedule_id}/reopen", response_model=MemberStatusResponse)
def reopen_for_adjustment(
    schedule_id: int,
    req: ReopenScheduleRequest,
    db: Session = Depends(get_db)
):
    """
    El Administrador acuerda un ajuste y reabre el horario del miembro a BORRADOR (HU-07).
    """
    status_entry = ScheduleService.reopen_for_adjustment(db, schedule_id, req.user_id, req.admin_id, req.notes)
    return status_entry

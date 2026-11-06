import datetime
import json
from typing import Dict, Any, List, Optional
from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models import (
    User, Team, TeamMember, ShiftBlockCatalog, WeeklySchedule,
    WeeklyScheduleMemberStatus, ShiftAssignment,
    RoleEnum, ScheduleStatusEnum, MemberWorkflowStateEnum, PeriodEnum, DurationTypeEnum
)

DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

class ScheduleService:

    @staticmethod
    def get_or_create_weekly_schedule(db: Session, team_id: int, year: int, week_number: int) -> WeeklySchedule:
        schedule = db.query(WeeklySchedule).filter(
            WeeklySchedule.team_id == team_id,
            WeeklySchedule.year == year,
            WeeklySchedule.week_number == week_number
        ).first()

        if not schedule:
            schedule = WeeklySchedule(
                team_id=team_id,
                year=year,
                week_number=week_number,
                status=ScheduleStatusEnum.DRAFT.value
            )
            db.add(schedule)
            try:
                db.commit()
                db.refresh(schedule)
            except IntegrityError:
                db.rollback()
                schedule = db.query(WeeklySchedule).filter(
                    WeeklySchedule.team_id == team_id,
                    WeeklySchedule.year == year,
                    WeeklySchedule.week_number == week_number
                ).first()
                if not schedule:
                    raise

        # Asegurar estados para todos los miembros SCHEDULER
        scheduler_members = db.query(TeamMember).filter(
            TeamMember.team_id == team_id,
            TeamMember.role == RoleEnum.SCHEDULER.value
        ).all()

        for member in scheduler_members:
            existing_status = db.query(WeeklyScheduleMemberStatus).filter(
                WeeklyScheduleMemberStatus.weekly_schedule_id == schedule.id,
                WeeklyScheduleMemberStatus.user_id == member.user_id
            ).first()
            if not existing_status:
                new_status = WeeklyScheduleMemberStatus(
                    weekly_schedule_id=schedule.id,
                    user_id=member.user_id,
                    workflow_state=MemberWorkflowStateEnum.DRAFT.value,
                    rejection_history=[]
                )
                db.add(new_status)
        try:
            db.commit()
            db.refresh(schedule)
        except IntegrityError:
            db.rollback()
            db.refresh(schedule)

        return schedule

    @staticmethod
    def get_week_dates(year: int, week_number: int) -> List[str]:
        # Obtener el lunes de la semana ISO oficial
        start_monday = datetime.date.fromisocalendar(year, week_number, 1)
        return [(start_monday + datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    @staticmethod
    def check_and_validate_quota(
        db: Session,
        schedule_id: int,
        user_id: int,
        date_str: str,
        shift_block_id: int,
        period: str = PeriodEnum.FULL_DAY.value,
        exclude_assignment_id: Optional[int] = None
    ):
        schedule = db.query(WeeklySchedule).filter(WeeklySchedule.id == schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="Horario semanal no encontrado")

        team = db.query(Team).filter(Team.id == schedule.team_id).first()
        block = db.query(ShiftBlockCatalog).filter(ShiftBlockCatalog.id == shift_block_id).first()
        if not block:
            raise HTTPException(status_code=404, detail="Bloque de turno no encontrado en el catálogo")

        # Verificar si el bloque requiere cobertura y si hay reglas para este día
        if not block.requires_coverage:
            return True

        rules_config = team.rules_config or {}
        quotas_config = rules_config.get("quotas", {})

        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        day_name = DAYS_OF_WEEK[date_obj.weekday()]

        day_quotas = quotas_config.get(day_name, {})
        max_limit = day_quotas.get(block.name)

        if max_limit is None:
            # Sin límite configurado para este bloque en este día
            return True

        # Contar asignaciones existentes del mismo bloque en la misma fecha (solo de miembros PUBLICADOS o CONFIRMADOS)
        query = db.query(ShiftAssignment).join(
            WeeklyScheduleMemberStatus,
            (WeeklyScheduleMemberStatus.weekly_schedule_id == ShiftAssignment.weekly_schedule_id) &
            (WeeklyScheduleMemberStatus.user_id == ShiftAssignment.user_id)
        ).filter(
            ShiftAssignment.weekly_schedule_id == schedule_id,
            ShiftAssignment.date == date_str,
            ShiftAssignment.shift_block_id == shift_block_id,
            ShiftAssignment.user_id != user_id,
            WeeklyScheduleMemberStatus.workflow_state.in_([
                MemberWorkflowStateEnum.SUBMITTED.value,
                MemberWorkflowStateEnum.CONFIRMED.value
            ])
        )
        if exclude_assignment_id:
            query = query.filter(ShiftAssignment.id != exclude_assignment_id)

        current_count = query.count()

        if current_count >= max_limit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cupo diario excedido para '{block.name}' el día {day_name} ({date_str}). Límite permitido: {max_limit}, ocupados: {current_count}."
            )
        return True

    @staticmethod
    def assign_shift(
        db: Session,
        schedule_id: int,
        user_id: int,
        date_str: str,
        shift_block_id: int,
        period: str = PeriodEnum.FULL_DAY.value,
        is_draft: bool = True,
        operator_id: Optional[int] = None
    ) -> ShiftAssignment:
        # Validar si es una fecha en el pasado respecto a hoy
        target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.date.today()
        if target_date < today:
            # Comprobar si el operador tiene privilegios de ADMIN
            is_admin = False
            effective_operator_id = operator_id or user_id
            operator_user = db.query(User).filter(User.id == effective_operator_id).first()
            if operator_user and operator_user.username == "admin":
                is_admin = True
            else:
                schedule = db.query(WeeklySchedule).filter(WeeklySchedule.id == schedule_id).first()
                if schedule:
                    op_member = db.query(TeamMember).filter(
                        TeamMember.team_id == schedule.team_id,
                        TeamMember.user_id == effective_operator_id
                    ).first()
                    if op_member and op_member.role == RoleEnum.ADMIN.value:
                        is_admin = True

            if not is_admin:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No está permitido agregar o modificar turnos en días anteriores a la fecha actual."
                )

        # Validar que el usuario es SCHEDULER del equipo
        schedule = db.query(WeeklySchedule).filter(WeeklySchedule.id == schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="Horario semanal no encontrado")

        member = db.query(TeamMember).filter(
            TeamMember.team_id == schedule.team_id,
            TeamMember.user_id == user_id
        ).first()

        if not member or member.role != RoleEnum.SCHEDULER.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo miembros con rol SCHEDULER pueden recibir o auto-asignar turnos en la grilla operativa (RN-01)."
            )

        # Validar permisos de edición según el estado del miembro (Confirmado / Publicado)
        status_entry = db.query(WeeklyScheduleMemberStatus).filter(
            WeeklyScheduleMemberStatus.weekly_schedule_id == schedule_id,
            WeeklyScheduleMemberStatus.user_id == user_id
        ).first()

        effective_operator_id = operator_id or user_id
        is_operator_admin = False
        op_user = db.query(User).filter(User.id == effective_operator_id).first()
        if op_user and op_user.username == "admin":
            is_operator_admin = True
        else:
            op_member = db.query(TeamMember).filter(
                TeamMember.team_id == schedule.team_id,
                TeamMember.user_id == effective_operator_id
            ).first()
            if op_member and op_member.role == RoleEnum.ADMIN.value:
                is_operator_admin = True

        if status_entry and status_entry.workflow_state == MemberWorkflowStateEnum.CONFIRMED.value and not is_operator_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No puedes modificar tus turnos una vez que has confirmado tu horario. Solo el Administrador puede realizar cambios."
            )

        if status_entry and status_entry.workflow_state == MemberWorkflowStateEnum.SUBMITTED.value and not is_operator_admin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tu propuesta se encuentra en estado PUBLICADO. Para modificar turnos debes presionar 'Borrador' primero."
            )

        # Validar tipo de jornada del bloque
        block = db.query(ShiftBlockCatalog).filter(ShiftBlockCatalog.id == shift_block_id).first()
        if not block:
            raise HTTPException(status_code=404, detail="Bloque no encontrado")

        if block.duration_type == DurationTypeEnum.FULL_DAY.value and period != PeriodEnum.FULL_DAY.value:
            period = PeriodEnum.FULL_DAY.value

        # En estado BORRADOR NO se validan ni consumen cupos diarios. Solo se valida si ya está publicado/confirmado.
        is_member_draft = is_draft or (status_entry and status_entry.workflow_state == MemberWorkflowStateEnum.DRAFT.value)
        if not is_member_draft:
            ScheduleService.check_and_validate_quota(db, schedule_id, user_id, date_str, shift_block_id, period)

        # Si es FULL_DAY, eliminar asignaciones previas en esa fecha para ese usuario (mañana/tarde o jornada completa)
        if period == PeriodEnum.FULL_DAY.value:
            db.query(ShiftAssignment).filter(
                ShiftAssignment.weekly_schedule_id == schedule_id,
                ShiftAssignment.user_id == user_id,
                ShiftAssignment.date == date_str
            ).delete()
        else:
            # Si es FIRST_HALF o SECOND_HALF, eliminar FULL_DAY previo o la misma mitad
            db.query(ShiftAssignment).filter(
                ShiftAssignment.weekly_schedule_id == schedule_id,
                ShiftAssignment.user_id == user_id,
                ShiftAssignment.date == date_str,
                ShiftAssignment.period.in_([period, PeriodEnum.FULL_DAY.value])
            ).delete()

        assignment = ShiftAssignment(
            weekly_schedule_id=schedule_id,
            user_id=user_id,
            date=date_str,
            shift_block_id=shift_block_id,
            period=period,
            is_draft=is_draft
        )
        db.add(assignment)
        db.commit()
        db.refresh(assignment)
        return assignment

    @staticmethod
    def publish_proposal(db: Session, schedule_id: int, user_id: int) -> WeeklyScheduleMemberStatus:
        status_entry = db.query(WeeklyScheduleMemberStatus).filter(
            WeeklyScheduleMemberStatus.weekly_schedule_id == schedule_id,
            WeeklyScheduleMemberStatus.user_id == user_id
        ).first()

        if not status_entry:
            raise HTTPException(status_code=404, detail="Estado del miembro no encontrado en esta semana")

        # 1. Obtener todas las asignaciones del usuario en esta semana
        user_assignments = db.query(ShiftAssignment).filter(
            ShiftAssignment.weekly_schedule_id == schedule_id,
            ShiftAssignment.user_id == user_id
        ).all()

        # 2. Validación de cupos diarios en lote para cada turno del usuario antes de publicar
        for a in user_assignments:
            ScheduleService.check_and_validate_quota(
                db=db,
                schedule_id=schedule_id,
                user_id=user_id,
                date_str=a.date,
                shift_block_id=a.shift_block_id,
                period=a.period,
                exclude_assignment_id=a.id
            )

        # 3. Si todos los cupos son válidos, marcar asignaciones como no borrador y actualizar estado
        for a in user_assignments:
            a.is_draft = False

        status_entry.workflow_state = MemberWorkflowStateEnum.SUBMITTED.value
        db.commit()
        db.refresh(status_entry)
        return status_entry

    @staticmethod
    def unpublish_to_draft(db: Session, schedule_id: int, user_id: int) -> WeeklyScheduleMemberStatus:
        status_entry = db.query(WeeklyScheduleMemberStatus).filter(
            WeeklyScheduleMemberStatus.weekly_schedule_id == schedule_id,
            WeeklyScheduleMemberStatus.user_id == user_id
        ).first()

        if not status_entry:
            raise HTTPException(status_code=404, detail="Estado del miembro no encontrado en esta semana")

        if status_entry.workflow_state == MemberWorkflowStateEnum.CONFIRMED.value:
            raise HTTPException(status_code=400, detail="No puedes devolver a borrador un horario que ya ha sido confirmado.")

        # Revertir asignaciones a borrador
        db.query(ShiftAssignment).filter(
            ShiftAssignment.weekly_schedule_id == schedule_id,
            ShiftAssignment.user_id == user_id
        ).update({"is_draft": True})

        status_entry.workflow_state = MemberWorkflowStateEnum.DRAFT.value
        db.commit()
        db.refresh(status_entry)
        return status_entry

    @staticmethod
    def consolidate_schedule(db: Session, schedule_id: int, admin_user_id: int) -> WeeklySchedule:
        schedule = db.query(WeeklySchedule).filter(WeeklySchedule.id == schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="Horario semanal no encontrado")

        # Verificar permisos de Admin
        admin_member = db.query(TeamMember).filter(
            TeamMember.team_id == schedule.team_id,
            TeamMember.user_id == admin_user_id,
            TeamMember.role == RoleEnum.ADMIN.value
        ).first()

        admin_user = db.query(User).filter(User.id == admin_user_id).first()
        is_admin = (admin_member is not None) or (admin_user and admin_user.username == "admin")

        if not is_admin:
            raise HTTPException(status_code=403, detail="Solo un Administrador del equipo puede consolidar el horario semanal")

        # Regla: Como ADMIN, solo puedo consolidar la semana cuando todos los usuarios con rol SCHEDULER del equipo confirmaron.
        schedulers = db.query(TeamMember).filter(
            TeamMember.team_id == schedule.team_id,
            TeamMember.role == RoleEnum.SCHEDULER.value
        ).all()

        unconfirmed_names = []
        for s in schedulers:
            status_entry = db.query(WeeklyScheduleMemberStatus).filter(
                WeeklyScheduleMemberStatus.weekly_schedule_id == schedule_id,
                WeeklyScheduleMemberStatus.user_id == s.user_id
            ).first()
            if not status_entry or status_entry.workflow_state != MemberWorkflowStateEnum.CONFIRMED.value:
                user_obj = db.query(User).filter(User.id == s.user_id).first()
                unconfirmed_names.append(user_obj.name if user_obj else f"Usuario #{s.user_id}")

        if unconfirmed_names:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No se puede consolidar la semana. Faltan confirmar: {', '.join(unconfirmed_names)}"
            )

        # Marcar asignaciones como definitivas (no borrador)
        db.query(ShiftAssignment).filter(
            ShiftAssignment.weekly_schedule_id == schedule_id
        ).update({"is_draft": False})

        schedule.status = ScheduleStatusEnum.CONSOLIDATED.value
        db.commit()
        db.refresh(schedule)
        return schedule

    @staticmethod
    def confirm_schedule(db: Session, schedule_id: int, user_id: int, comment: Optional[str] = None) -> WeeklyScheduleMemberStatus:
        schedule = db.query(WeeklySchedule).filter(WeeklySchedule.id == schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="Horario semanal no encontrado")

        # Validar que es SCHEDULER
        member = db.query(TeamMember).filter(
            TeamMember.team_id == schedule.team_id,
            TeamMember.user_id == user_id,
            TeamMember.role == RoleEnum.SCHEDULER.value
        ).first()
        if not member:
            raise HTTPException(status_code=403, detail="Exclusividad: Solo miembros con rol SCHEDULER pueden confirmar el horario (RN-04)")

        status_entry = db.query(WeeklyScheduleMemberStatus).filter(
            WeeklyScheduleMemberStatus.weekly_schedule_id == schedule_id,
            WeeklyScheduleMemberStatus.user_id == user_id
        ).first()

        if not status_entry:
            raise HTTPException(status_code=404, detail="Estado del miembro no encontrado")

        status_entry.workflow_state = MemberWorkflowStateEnum.CONFIRMED.value
        status_entry.feedback_comment = comment
        status_entry.confirmed_at = datetime.datetime.now(datetime.timezone.utc)
        db.commit()
        db.refresh(status_entry)
        return status_entry

    @staticmethod
    def reject_schedule(db: Session, schedule_id: int, user_id: int, reason: str) -> WeeklyScheduleMemberStatus:
        if not reason or len(reason.strip()) < 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Es obligatorio ingresar un motivo o justificación de discrepancia para rechazar el horario (RN-05, HU-07)."
            )

        schedule = db.query(WeeklySchedule).filter(WeeklySchedule.id == schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="Horario semanal no encontrado")

        if schedule.status == ScheduleStatusEnum.CONSOLIDATED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se puede rechazar un horario que ya ha sido consolidado por el Administrador."
            )

        status_entry = db.query(WeeklyScheduleMemberStatus).filter(
            WeeklyScheduleMemberStatus.weekly_schedule_id == schedule_id,
            WeeklyScheduleMemberStatus.user_id == user_id
        ).first()

        if not status_entry:
            raise HTTPException(status_code=404, detail="Estado del miembro no encontrado")

        if status_entry.workflow_state == MemberWorkflowStateEnum.CONFIRMED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No puedes rechazar un horario que ya has confirmado previamente."
            )

        # Regla: Solo se permite rechazar si al menos otro colaborador SCHEDULER del equipo ha publicado su propuesta
        other_published = db.query(WeeklyScheduleMemberStatus).join(
            TeamMember,
            (TeamMember.team_id == schedule.team_id) &
            (TeamMember.user_id == WeeklyScheduleMemberStatus.user_id)
        ).filter(
            WeeklyScheduleMemberStatus.weekly_schedule_id == schedule_id,
            WeeklyScheduleMemberStatus.user_id != user_id,
            TeamMember.role == RoleEnum.SCHEDULER.value,
            WeeklyScheduleMemberStatus.workflow_state.in_([
                MemberWorkflowStateEnum.SUBMITTED.value,
                MemberWorkflowStateEnum.CONFIRMED.value
            ])
        ).first()

        if not other_published:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Solo puedes rechazar el horario si al menos otro miembro del equipo ha publicado su propuesta."
            )

        history = list(status_entry.rejection_history or [])
        history.append({
            "reason": reason.strip(),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "action": "REJECTED"
        })

        status_entry.workflow_state = MemberWorkflowStateEnum.REJECTED.value
        status_entry.feedback_comment = reason.strip()
        status_entry.rejection_history = history
        db.commit()
        db.refresh(status_entry)
        return status_entry

    @staticmethod
    def revert_rejection_to_confirmed(db: Session, schedule_id: int, user_id: int, comment: Optional[str] = None) -> WeeklyScheduleMemberStatus:
        status_entry = db.query(WeeklyScheduleMemberStatus).filter(
            WeeklyScheduleMemberStatus.weekly_schedule_id == schedule_id,
            WeeklyScheduleMemberStatus.user_id == user_id
        ).first()

        if not status_entry:
            raise HTTPException(status_code=404, detail="Estado del miembro no encontrado")

        if status_entry.workflow_state != MemberWorkflowStateEnum.REJECTED.value:
            raise HTTPException(status_code=400, detail="Solo se puede revertir un horario que se encuentre en estado RECHAZADO")

        history = list(status_entry.rejection_history or [])
        history.append({
            "reason": comment or "Miembro reconsidera y aprueba",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "action": "REVERTED_TO_CONFIRMED"
        })

        status_entry.workflow_state = MemberWorkflowStateEnum.CONFIRMED.value
        status_entry.feedback_comment = comment or "Conformidad otorgada tras reconsideración"
        status_entry.confirmed_at = datetime.datetime.now(datetime.timezone.utc)
        status_entry.rejection_history = history
        db.commit()
        db.refresh(status_entry)
        return status_entry

    @staticmethod
    def reopen_for_adjustment(db: Session, schedule_id: int, user_id: int, admin_id: int, notes: Optional[str] = None) -> WeeklyScheduleMemberStatus:
        schedule = db.query(WeeklySchedule).filter(WeeklySchedule.id == schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="Horario semanal no encontrado")

        admin_member = db.query(TeamMember).filter(
            TeamMember.team_id == schedule.team_id,
            TeamMember.user_id == admin_id,
            TeamMember.role == RoleEnum.ADMIN.value
        ).first()
        if not admin_member:
            raise HTTPException(status_code=403, detail="Solo un Administrador puede reabrir el horario para ajuste")

        status_entry = db.query(WeeklyScheduleMemberStatus).filter(
            WeeklyScheduleMemberStatus.weekly_schedule_id == schedule_id,
            WeeklyScheduleMemberStatus.user_id == user_id
        ).first()

        if not status_entry:
            raise HTTPException(status_code=404, detail="Estado del miembro no encontrado")

        history = list(status_entry.rejection_history or [])
        history.append({
            "notes": notes or "Reapertura acordada por Administrador",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "action": "REOPENED_BY_ADMIN",
            "admin_id": admin_id
        })

        status_entry.workflow_state = MemberWorkflowStateEnum.DRAFT.value
        status_entry.rejection_history = history
        db.commit()
        db.refresh(status_entry)
        return status_entry

    @staticmethod
    def get_grid_data(db: Session, team_id: int, year: int, week_number: int, current_user_id: Optional[int] = None) -> Dict[str, Any]:
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team:
            raise HTTPException(status_code=404, detail="Equipo no encontrado")

        schedule = ScheduleService.get_or_create_weekly_schedule(db, team_id, year, week_number)
        dates = ScheduleService.get_week_dates(year, week_number)

        # RN-01 & HU-04: En la grilla compartida, CADA FILA representa ÚNICA Y EXCLUSIVAMENTE a miembros con rol SCHEDULER
        scheduler_members = db.query(TeamMember).filter(
            TeamMember.team_id == team_id,
            TeamMember.role == RoleEnum.SCHEDULER.value
        ).all()

        rows = []
        for member in scheduler_members:
            status_entry = db.query(WeeklyScheduleMemberStatus).filter(
                WeeklyScheduleMemberStatus.weekly_schedule_id == schedule.id,
                WeeklyScheduleMemberStatus.user_id == member.user_id
            ).first()

            if not status_entry:
                status_entry = WeeklyScheduleMemberStatus(
                    weekly_schedule_id=schedule.id,
                    user_id=member.user_id,
                    workflow_state=MemberWorkflowStateEnum.DRAFT.value,
                    rejection_history=[]
                )
                db.add(status_entry)
                try:
                    db.commit()
                    db.refresh(status_entry)
                except IntegrityError:
                    db.rollback()
                    status_entry = db.query(WeeklyScheduleMemberStatus).filter(
                        WeeklyScheduleMemberStatus.weekly_schedule_id == schedule.id,
                        WeeklyScheduleMemberStatus.user_id == member.user_id
                    ).first()

            if status_entry:
                if status_entry.rejection_history is None:
                    status_entry.rejection_history = []
                elif isinstance(status_entry.rejection_history, str):
                    try:
                        status_entry.rejection_history = json.loads(status_entry.rejection_history)
                    except Exception:
                        status_entry.rejection_history = []

            is_self = (current_user_id is not None and int(member.user_id) == int(current_user_id))
            is_published = status_entry and (status_entry.workflow_state in [
                MemberWorkflowStateEnum.SUBMITTED.value,
                MemberWorkflowStateEnum.CONFIRMED.value
            ])

            # En estado BORRADOR, el usuario scheduler puede visualizar solo en su vista sus bloques.
            # Los demás usuarios (incluido Admin) solo ven los bloques cuando el usuario publica su horario.
            if is_self or is_published:
                assignments = db.query(ShiftAssignment).filter(
                    ShiftAssignment.weekly_schedule_id == schedule.id,
                    ShiftAssignment.user_id == member.user_id
                ).all()
            else:
                assignments = []

            rows.append({
                "user": member.user,
                "role": member.role,
                "status": status_entry,
                "assignments": assignments
            })

        # Optimización: 1 sola consulta agregada GROUP BY en lugar de 35 queries individuales
        catalog = db.query(ShiftBlockCatalog).all()
        rules_config = team.rules_config or {}
        if isinstance(rules_config, str):
            try:
                rules_config = json.loads(rules_config)
            except Exception:
                rules_config = {}
        quotas_config = rules_config.get("quotas", {})

        counts_query = db.query(
            ShiftAssignment.date,
            ShiftAssignment.shift_block_id,
            func.count(ShiftAssignment.id).label("used_count")
        ).join(
            WeeklyScheduleMemberStatus,
            (WeeklyScheduleMemberStatus.weekly_schedule_id == ShiftAssignment.weekly_schedule_id) &
            (WeeklyScheduleMemberStatus.user_id == ShiftAssignment.user_id)
        ).filter(
            ShiftAssignment.weekly_schedule_id == schedule.id,
            WeeklyScheduleMemberStatus.workflow_state.in_([
                MemberWorkflowStateEnum.SUBMITTED.value,
                MemberWorkflowStateEnum.CONFIRMED.value
            ])
        ).group_by(
            ShiftAssignment.date,
            ShiftAssignment.shift_block_id
        ).all()

        usage_map = {(row.date, row.shift_block_id): row.used_count for row in counts_query}

        daily_quotas = {}
        for date_str in dates:
            date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            day_name = DAYS_OF_WEEK[date_obj.weekday()]
            day_rules = quotas_config.get(day_name, {})

            block_quota_map = {}
            for block in catalog:
                limit = day_rules.get(block.name, None)
                used = usage_map.get((date_str, block.id), 0)
                available = (limit - used) if limit is not None else None
                block_quota_map[block.name] = {
                    "limit": limit,
                    "used": used,
                    "available": available,
                    "is_full": (available is not None and available <= 0)
                }
            daily_quotas[date_str] = {
                "day_name": day_name,
                "quotas": block_quota_map
            }

        return {
            "schedule": schedule,
            "team": team,
            "dates": dates,
            "quotas": daily_quotas,
            "rows": rows,
            "catalog": catalog
        }

    @staticmethod
    def export_consolidated_shifts(
        db: Session,
        team_id: int,
        start_date: str,
        end_date: str,
        format_type: str = "xlsx"
    ):
        """
        Exporta los turnos consolidados de un equipo dentro de un rango de fechas (mes/meses)
        utilizando la plantilla matricial 'Propuesta 1 - Semanal'.
        Solo incluye asignaciones pertenecientes a semanas que han sido consolidadas/publicadas por el ADMIN.
        """
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team:
            raise HTTPException(status_code=404, detail="Equipo no encontrado")

        # 1. Determinar lista ordenada de semanas ISO en el rango
        try:
            start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
            end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
        except Exception:
            raise HTTPException(status_code=400, detail="Formato de fecha inválido. Usar YYYY-MM-DD")

        if start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt

        weeks_seen = set()
        weeks_list = []  # [(iso_year, iso_week), ...]
        cur = start_dt
        while cur <= end_dt:
            iso_year, iso_week, _ = cur.isocalendar()
            key = (iso_year, iso_week)
            if key not in weeks_seen:
                weeks_seen.add(key)
                weeks_list.append(key)
            cur += datetime.timedelta(days=1)

        if not weeks_list:
            iso_year, iso_week, _ = start_dt.isocalendar()
            weeks_list.append((iso_year, iso_week))

        MONTH_ABBRS = {
            1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
            7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"
        }
        DAY_NAMES_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

        # 2. Información de cada semana: fechas y si está consolidada
        week_info_list = []
        for y, w in weeks_list:
            dates = ScheduleService.get_week_dates(y, w)
            d_start = datetime.date.fromisoformat(dates[0])
            d_end = datetime.date.fromisoformat(dates[6])
            label = f"Semana {w} ({d_start.day:02d} {MONTH_ABBRS[d_start.month]} - {d_end.day:02d} {MONTH_ABBRS[d_end.month]}) - Año {y}"

            sched = db.query(WeeklySchedule).filter(
                WeeklySchedule.team_id == team_id,
                WeeklySchedule.year == y,
                WeeklySchedule.week_number == w,
                WeeklySchedule.status == ScheduleStatusEnum.CONSOLIDATED.value
            ).first()

            assignments_map = {}  # (user_id, date_str) -> list of ShiftBlockCatalog
            if sched:
                shifts = db.query(ShiftAssignment).filter(
                    ShiftAssignment.weekly_schedule_id == sched.id
                ).all()
                for s in shifts:
                    block = db.query(ShiftBlockCatalog).filter(ShiftBlockCatalog.id == s.shift_block_id).first()
                    if block:
                        assignments_map.setdefault((s.user_id, s.date), []).append(block)

            week_info_list.append({
                "year": y,
                "week_number": w,
                "dates": dates,
                "label": label,
                "is_consolidated": bool(sched),
                "assignments_map": assignments_map
            })

        # 3. Obtener miembros con rol SCHEDULER del equipo
        scheduler_members = db.query(TeamMember).filter(
            TeamMember.team_id == team_id,
            TeamMember.role == RoleEnum.SCHEDULER.value
        ).all()

        members = []
        for tm in scheduler_members:
            u = db.query(User).filter(User.id == tm.user_id).first()
            if u:
                members.append(u)
        members.sort(key=lambda x: x.name)

        # 4. Exportar según formato (CSV o XLSX)
        if format_type == "csv":
            import io, csv
            output = io.StringIO()
            writer = csv.writer(output, delimiter=';')

            # Fila 1: Cabecera con semanas
            row1 = ["Equipo", "Colaborador"]
            for winfo in week_info_list:
                row1.append(winfo["label"])
                row1.extend([""] * 6)
            writer.writerow(row1)

            # Fila 2: Días de la semana
            row2 = ["", ""]
            for winfo in week_info_list:
                row2.extend(DAY_NAMES_ES)
            writer.writerow(row2)

            # Fila 3: Fechas YYYY-MM-DD
            row3 = ["", ""]
            for winfo in week_info_list:
                row3.extend(winfo["dates"])
            writer.writerow(row3)

            # Filas de colaboradores
            for user in members:
                row = [team.name, user.name]
                for winfo in week_info_list:
                    amap = winfo["assignments_map"]
                    for d in winfo["dates"]:
                        blocks = amap.get((user.id, d), [])
                        if blocks:
                            val = " / ".join([b.name for b in blocks])
                        else:
                            val = ""
                        row.append(val)
                writer.writerow(row)

            output.seek(0)
            return output.getvalue().encode('utf-8-sig'), "text/csv", f"turnos_consolidados_{start_date}_{end_date}.csv"
        else:
            # Excel con openpyxl
            import io
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            from openpyxl.utils import get_column_letter

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Propuesta 1 - Semanal"

            # Estilos
            thin_border = Border(
                left=Side(style='thin', color='CBD5E1'),
                right=Side(style='thin', color='CBD5E1'),
                top=Side(style='thin', color='CBD5E1'),
                bottom=Side(style='thin', color='CBD5E1')
            )
            header_team_fill = PatternFill(start_color="15803D", end_color="15803D", fill_type="solid")
            header_team_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

            header_week_fill = PatternFill(start_color="166534", end_color="166534", fill_type="solid")
            header_week_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

            day_name_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
            day_name_font = Font(name="Calibri", size=10, bold=True, color="1E293B")

            date_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
            date_font = Font(name="Calibri", size=9, bold=True, color="475569")

            SHIFT_EXCEL_STYLES = {
                "REMOTO": {"fill": "DBEAFE", "font_color": "1E40AF"},
                "PRESENCIAL": {"fill": "DCFCE7", "font_color": "166534"},
                "MEDIO DÍA": {"fill": "FEF3C7", "font_color": "92400E"},
                "PERMISO": {"fill": "F3E8FF", "font_color": "6B21A8"},
                "DÍA LIBRE": {"fill": "F1F5F9", "font_color": "475569"},
            }

            ws.row_dimensions[1].height = 28
            ws.row_dimensions[2].height = 22
            ws.row_dimensions[3].height = 20

            # Celdas fijas A1:A3 (Equipo) y B1:B3 (Colaborador)
            ws["A1"] = "Equipo"
            ws["B1"] = "Colaborador"

            for col_idx in [1, 2]:
                for r in range(1, 4):
                    c = ws.cell(row=r, column=col_idx)
                    c.fill = header_team_fill
                    c.font = header_team_font
                    c.alignment = Alignment(horizontal="center", vertical="center")
                    c.border = thin_border
            ws.merge_cells("A1:A3")
            ws.merge_cells("B1:B3")

            # Columnas de semanas
            col_cursor = 3
            for winfo in week_info_list:
                start_c = col_cursor
                end_c = col_cursor + 6

                # Fila 1: Semana
                ws.cell(row=1, column=start_c, value=winfo["label"])
                ws.merge_cells(start_row=1, start_column=start_c, end_row=1, end_column=end_c)
                for col in range(start_c, end_c + 1):
                    c = ws.cell(row=1, column=col)
                    c.fill = header_week_fill
                    c.font = header_week_font
                    c.alignment = Alignment(horizontal="center", vertical="center")
                    c.border = thin_border

                # Fila 2: Nombres de los días
                # Fila 3: Fechas YYYY-MM-DD
                for d_idx, day_name in enumerate(DAY_NAMES_ES):
                    current_c = start_c + d_idx
                    # Fila 2
                    c2 = ws.cell(row=2, column=current_c, value=day_name)
                    c2.fill = day_name_fill
                    c2.font = day_name_font
                    c2.alignment = Alignment(horizontal="center", vertical="center")
                    c2.border = thin_border

                    # Fila 3
                    c3 = ws.cell(row=3, column=current_c, value=winfo["dates"][d_idx])
                    c3.fill = date_fill
                    c3.font = date_font
                    c3.alignment = Alignment(horizontal="center", vertical="center")
                    c3.border = thin_border

                col_cursor += 7

            # Filas de colaboradores
            row_idx = 4
            for user in members:
                ws.row_dimensions[row_idx].height = 24
                # Col A: Equipo
                cA = ws.cell(row=row_idx, column=1, value=team.name)
                cA.font = Font(name="Calibri", size=10, bold=False, color="1E293B")
                cA.alignment = Alignment(horizontal="left", vertical="center")
                cA.border = thin_border

                # Col B: Colaborador
                cB = ws.cell(row=row_idx, column=2, value=user.name)
                cB.font = Font(name="Calibri", size=10, bold=True, color="0F172A")
                cB.alignment = Alignment(horizontal="left", vertical="center")
                cB.border = thin_border

                # Días
                col_cursor = 3
                for winfo in week_info_list:
                    amap = winfo["assignments_map"]
                    for d in winfo["dates"]:
                        c = ws.cell(row=row_idx, column=col_cursor)
                        c.border = thin_border
                        c.alignment = Alignment(horizontal="center", vertical="center")

                        blocks = amap.get((user.id, d), [])
                        if blocks:
                            val = " / ".join([b.name for b in blocks])
                            c.value = val

                            primary_name = blocks[0].name
                            if primary_name in SHIFT_EXCEL_STYLES:
                                style_info = SHIFT_EXCEL_STYLES[primary_name]
                                c.fill = PatternFill(start_color=style_info["fill"], end_color=style_info["fill"], fill_type="solid")
                                c.font = Font(name="Calibri", size=10, bold=True, color=style_info["font_color"])
                            else:
                                hex_col = (blocks[0].color or "64748B").replace('#', '')
                                if len(hex_col) == 6:
                                    try:
                                        c.fill = PatternFill(start_color=hex_col, end_color=hex_col, fill_type="solid")
                                        c.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
                                    except Exception:
                                        c.font = Font(name="Calibri", size=10, bold=True)
                                else:
                                    c.font = Font(name="Calibri", size=10, bold=True)
                        else:
                            c.value = ""
                            c.font = Font(name="Calibri", size=10)

                        col_cursor += 1

                row_idx += 1

            if not members:
                ws.row_dimensions[4].height = 24
                ws.cell(row=4, column=1, value=team.name).border = thin_border
                ws.cell(row=4, column=2, value="Sin colaboradores con rol SCHEDULER").border = thin_border
                for col_idx in range(3, col_cursor):
                    ws.cell(row=4, column=col_idx, value="").border = thin_border

            # Ajustar anchos de columnas
            ws.column_dimensions['A'].width = max(len(team.name) + 4, 18)
            max_user_len = max([len(u.name) for u in members], default=14)
            ws.column_dimensions['B'].width = max(max_user_len + 4, 20)

            for col_idx in range(3, col_cursor):
                col_letter = get_column_letter(col_idx)
                ws.column_dimensions[col_letter].width = 14

            file_stream = io.BytesIO()
            wb.save(file_stream)
            file_stream.seek(0)
            return file_stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"turnos_consolidados_{start_date}_{end_date}.xlsx"

import datetime
from typing import Dict, Any, List, Optional
from fastapi import HTTPException, status
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
            db.commit()
            db.refresh(schedule)

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
        db.commit()
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
        schedule = ScheduleService.get_or_create_weekly_schedule(db, team_id, year, week_number)
        team = db.query(Team).filter(Team.id == team_id).first()
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

            if status_entry and status_entry.rejection_history is None:
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

        # Calcular uso de cupos diarios por fecha (solo de turnos PUBLICADOS o CONFIRMADOS)
        catalog = db.query(ShiftBlockCatalog).all()
        rules_config = team.rules_config or {}
        quotas_config = rules_config.get("quotas", {})

        daily_quotas = {}
        for date_str in dates:
            date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            day_name = DAYS_OF_WEEK[date_obj.weekday()]
            day_rules = quotas_config.get(day_name, {})

            block_quota_map = {}
            for block in catalog:
                limit = day_rules.get(block.name, None)
                used = db.query(ShiftAssignment).join(
                    WeeklyScheduleMemberStatus,
                    (WeeklyScheduleMemberStatus.weekly_schedule_id == ShiftAssignment.weekly_schedule_id) &
                    (WeeklyScheduleMemberStatus.user_id == ShiftAssignment.user_id)
                ).filter(
                    ShiftAssignment.weekly_schedule_id == schedule.id,
                    ShiftAssignment.date == date_str,
                    ShiftAssignment.shift_block_id == block.id,
                    WeeklyScheduleMemberStatus.workflow_state.in_([
                        MemberWorkflowStateEnum.SUBMITTED.value,
                        MemberWorkflowStateEnum.CONFIRMED.value
                    ])
                ).count()
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
        Exporta los turnos consolidados de un equipo dentro de un rango de fechas (mes/meses).
        Solo incluye asignaciones pertenecientes a semanas que han sido consolidadas/publicadas por el ADMIN.
        """
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team:
            raise HTTPException(status_code=404, detail="Equipo no encontrado")

        # Buscar semanas consolidadas del equipo
        consolidated_schedules = db.query(WeeklySchedule).filter(
            WeeklySchedule.team_id == team_id,
            WeeklySchedule.status == ScheduleStatusEnum.CONSOLIDATED.value
        ).all()
        consolidated_schedule_ids = [s.id for s in consolidated_schedules]

        # Si no hay semanas consolidadas, retornar lista vacía
        assignments = []
        if consolidated_schedule_ids:
            assignments = db.query(ShiftAssignment).filter(
                ShiftAssignment.weekly_schedule_id.in_(consolidated_schedule_ids),
                ShiftAssignment.date >= start_date,
                ShiftAssignment.date <= end_date
            ).order_by(ShiftAssignment.date.asc(), ShiftAssignment.user_id.asc()).all()

        records = []
        for a in assignments:
            user = db.query(User).filter(User.id == a.user_id).first()
            block = db.query(ShiftBlockCatalog).filter(ShiftBlockCatalog.id == a.shift_block_id).first()
            try:
                date_obj = datetime.datetime.strptime(a.date, "%Y-%m-%d").date()
                day_name = DAYS_OF_WEEK[date_obj.weekday()]
            except Exception:
                day_name = ""

            records.append({
                "team_name": team.name,
                "user_name": user.name if user else f"Usuario #{a.user_id}",
                "user_email": user.email if user else "",
                "date": a.date,
                "day_name": day_name,
                "shift_name": block.name if block else "Desconocido",
                "shift_color": block.color if block else "#64748b",
                "duration_type": "Día Completo" if block and block.duration_type == "FULL_DAY" else "Media Jornada",
                "period": "Jornada Completa" if a.period == "FULL_DAY" else ("Mañana" if a.period == "FIRST_HALF" else "Tarde"),
                "status": "CONSOLIDADO / PUBLICADO"
            })

        if format_type == "csv":
            import io, csv
            output = io.StringIO()
            writer = csv.writer(output, delimiter=';')
            writer.writerow(["Equipo", "Colaborador", "Email", "Fecha", "Día", "Turno", "Tipo Jornada", "Período", "Estado"])
            for r in records:
                writer.writerow([
                    r["team_name"], r["user_name"], r["user_email"],
                    r["date"], r["day_name"], r["shift_name"],
                    r["duration_type"], r["period"], r["status"]
                ])
            output.seek(0)
            return output.getvalue().encode('utf-8-sig'), "text/csv", f"turnos_consolidados_{start_date}_{end_date}.csv"
        else:
            # Generación Excel (.xlsx) con openpyxl estilizado
            import io
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Turnos Consolidados"

            # Título institucional
            ws.merge_cells("A1:I1")
            title_cell = ws["A1"]
            title_cell.value = f"NoTI - Reporte de Turnos Consolidados ({team.name})"
            title_cell.font = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
            title_cell.fill = PatternFill(start_color="15803D", end_color="15803D", fill_type="solid")
            title_cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.row_dimensions[1].height = 32

            # Subtítulo con rango y nota
            ws.merge_cells("A2:I2")
            sub_cell = ws["A2"]
            sub_cell.value = f"Período: {start_date} al {end_date} | Semanas validadas y publicadas por Administración"
            sub_cell.font = Font(name="Calibri", size=10, italic=True, color="334155")
            sub_cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.row_dimensions[2].height = 18

            # Encabezados de tabla
            headers = ["Equipo", "Colaborador", "Email", "Fecha", "Día", "Turno Asignado", "Tipo Jornada", "Período", "Estado"]
            ws.append([]) # Fila 3 vacía
            ws.append(headers) # Fila 4
            ws.row_dimensions[4].height = 24

            thin_border = Border(
                left=Side(style='thin', color='CBD5E1'),
                right=Side(style='thin', color='CBD5E1'),
                top=Side(style='thin', color='CBD5E1'),
                bottom=Side(style='thin', color='CBD5E1')
            )
            header_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
            header_font = Font(name="Calibri", size=10, bold=True, color="1E293B")

            for col_num in range(1, 10):
                cell = ws.cell(row=4, column=col_num)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = thin_border

            # Filas de datos
            row_idx = 5
            for r in records:
                ws.append([
                    r["team_name"], r["user_name"], r["user_email"],
                    r["date"], r["day_name"], r["shift_name"],
                    r["duration_type"], r["period"], r["status"]
                ])
                ws.row_dimensions[row_idx].height = 20

                for col_idx in range(1, 10):
                    c = ws.cell(row=row_idx, column=col_idx)
                    c.font = Font(name="Calibri", size=10)
                    c.border = thin_border
                    if col_idx in [4, 5, 7, 8, 9]:
                        c.alignment = Alignment(horizontal="center", vertical="center")
                    elif col_idx == 6:
                        c.alignment = Alignment(horizontal="center", vertical="center")
                        hex_col = r["shift_color"].replace('#', '')
                        if len(hex_col) == 6:
                            try:
                                c.fill = PatternFill(start_color=hex_col, end_color=hex_col, fill_type="solid")
                                c.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
                            except Exception:
                                pass
                row_idx += 1

            if len(records) == 0:
                ws.append(["No hay turnos consolidados en el rango de fechas seleccionado."])
                ws.merge_cells(f"A5:I5")
                empty_cell = ws["A5"]
                empty_cell.font = Font(name="Calibri", size=11, italic=True, color="64748B")
                empty_cell.alignment = Alignment(horizontal="center", vertical="center")

            # Ajustar anchos de columnas
            from openpyxl.utils import get_column_letter
            for col_idx, col in enumerate(ws.columns, start=1):
                max_len = 0
                col_letter = get_column_letter(col_idx)
                for cell in col:
                    if cell.row > 2 and cell.value:
                        max_len = max(max_len, len(str(cell.value)))
                ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

            file_stream = io.BytesIO()
            wb.save(file_stream)
            file_stream.seek(0)
            return file_stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"turnos_consolidados_{start_date}_{end_date}.xlsx"

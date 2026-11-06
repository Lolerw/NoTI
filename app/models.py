import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, ForeignKey, Text, JSON, DateTime, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.database import Base

class RoleEnum(str, enum.Enum):
    ADMIN = "ADMIN"
    SCHEDULER = "SCHEDULER"
    VISITANTE = "VISITANTE"

class DurationTypeEnum(str, enum.Enum):
    FULL_DAY = "FULL_DAY"
    HALF_DAY = "HALF_DAY"

class ScheduleStatusEnum(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    CONSOLIDATED = "CONSOLIDATED"
    CLOSED = "CLOSED"

class MemberWorkflowStateEnum(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"

class PeriodEnum(str, enum.Enum):
    FULL_DAY = "FULL_DAY"
    FIRST_HALF = "FIRST_HALF"    # Mañana
    SECOND_HALF = "SECOND_HALF"  # Tarde

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(120), nullable=False)
    email = Column(String(150), unique=True, index=True, nullable=False)
    avatar_url = Column(String(255), nullable=True)

    memberships = relationship("TeamMember", back_populates="user", cascade="all, delete-orphan")
    shift_assignments = relationship("ShiftAssignment", back_populates="user", cascade="all, delete-orphan")
    member_statuses = relationship("WeeklyScheduleMemberStatus", back_populates="user", cascade="all, delete-orphan")

class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    rules_config = Column(JSON, default=dict)  # Ej: {"quotas": {"Monday": {"REMOTO": 1}, "Tuesday": ...}}

    members = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")
    weekly_schedules = relationship("WeeklySchedule", back_populates="team", cascade="all, delete-orphan")

class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint('team_id', 'user_id', name='uq_team_user'),)

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False, default=RoleEnum.SCHEDULER.value)
    participates_in_schedule = Column(Boolean, default=True)

    team = relationship("Team", back_populates="members")
    user = relationship("User", back_populates="memberships")

class ShiftBlockCatalog(Base):
    __tablename__ = "shift_block_catalog"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    color = Column(String(20), nullable=False, default="#3B82F6")
    duration_type = Column(String(20), nullable=False, default=DurationTypeEnum.FULL_DAY.value)
    requires_coverage = Column(Boolean, default=True)

    assignments = relationship("ShiftAssignment", back_populates="shift_block")

class WeeklySchedule(Base):
    __tablename__ = "weekly_schedules"
    __table_args__ = (UniqueConstraint('team_id', 'year', 'week_number', name='uq_team_week'),)

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    year = Column(Integer, nullable=False)
    week_number = Column(Integer, nullable=False)
    status = Column(String(20), default=ScheduleStatusEnum.DRAFT.value)

    team = relationship("Team", back_populates="weekly_schedules")
    member_statuses = relationship("WeeklyScheduleMemberStatus", back_populates="weekly_schedule", cascade="all, delete-orphan")
    assignments = relationship("ShiftAssignment", back_populates="weekly_schedule", cascade="all, delete-orphan")

class WeeklyScheduleMemberStatus(Base):
    __tablename__ = "weekly_schedule_member_status"
    __table_args__ = (UniqueConstraint('weekly_schedule_id', 'user_id', name='uq_schedule_user_status'),)

    id = Column(Integer, primary_key=True, index=True)
    weekly_schedule_id = Column(Integer, ForeignKey("weekly_schedules.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    workflow_state = Column(String(20), default=MemberWorkflowStateEnum.DRAFT.value)
    feedback_comment = Column(Text, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    rejection_history = Column(JSON, default=list)  # [{"reason": "...", "timestamp": "...", "by_user_id": 1}]

    weekly_schedule = relationship("WeeklySchedule", back_populates="member_statuses")
    user = relationship("User", back_populates="member_statuses")

class ShiftAssignment(Base):
    __tablename__ = "shift_assignments"
    __table_args__ = (
        UniqueConstraint('weekly_schedule_id', 'user_id', 'date', 'period', name='uq_assignment_user_date_period'),
    )

    id = Column(Integer, primary_key=True, index=True)
    weekly_schedule_id = Column(Integer, ForeignKey("weekly_schedules.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    date = Column(String(10), nullable=False) # Formato YYYY-MM-DD
    shift_block_id = Column(Integer, ForeignKey("shift_block_catalog.id", ondelete="RESTRICT"), nullable=False)
    period = Column(String(20), default=PeriodEnum.FULL_DAY.value)
    is_draft = Column(Boolean, default=True)

    weekly_schedule = relationship("WeeklySchedule", back_populates="assignments")
    user = relationship("User", back_populates="shift_assignments")
    shift_block = relationship("ShiftBlockCatalog", back_populates="assignments")

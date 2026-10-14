import uuid

from sqlalchemy import (
    Column,
    Text,
    DateTime,
    Enum,
    ForeignKey,
    Integer
)

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.enums import ProgrammingLanguage


class Submission(Base):
    __tablename__ = "submissions"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    assignment_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assignments.id",
            ondelete="CASCADE"
        ),
        nullable=False,
        index=True
    )

    student_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE"
        ),
        nullable=False,
        index=True
    )

    submission_number = Column(
        Integer,
        nullable=False
    )

    source_code = Column(
        Text,
        nullable=False
    )

    language = Column(
        Enum(
            ProgrammingLanguage,
            name="submission_language"
        ),
        nullable=False
    )

    status = Column(
        Enum(
            "SUBMITTED",
            "QUEUED",
            "GRADING",
            "GRADED",
            "ESCALATED",
            "FAILED",
            name="submission_status"
        ),
        nullable=False,
        default="SUBMITTED"
    )

    submitted_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    assignment = relationship(
        "Assignment",
        back_populates="submissions"
    )

    student = relationship(
        "User",
        back_populates="submissions"
    )
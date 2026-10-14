import uuid

from sqlalchemy import (
    Column,
    String,
    Text,
    DateTime,
    Enum,
    ForeignKey
)

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.enums import (
    ProgrammingLanguage,
    AssignmentStatus
)


class Assignment(Base):
    __tablename__ = "assignments"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False
    )

    title = Column(
        String(255),
        nullable=False
    )

    description = Column(
        Text,
        nullable=False
    )

    language = Column(
        Enum(
            ProgrammingLanguage,
            name="programming_language"
        ),
        nullable=False
    )

    constraints = Column(Text)

    input_format = Column(Text)

    output_format = Column(Text)

    status = Column(
        Enum(
            AssignmentStatus,
            name="assignment_status"
        ),
        nullable=False,
        default=AssignmentStatus.DRAFT
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    creator = relationship(
        "User",
        back_populates="assignments"
    )

    rubrics = relationship(
        "Rubric",
        back_populates="assignment",
        cascade="all, delete-orphan"
    )

    test_cases = relationship(
        "TestCase",
        back_populates="assignment",
        cascade="all, delete-orphan"
    )

    reference_solution = relationship(
        "ReferenceSolution",
        back_populates="assignment",
        uselist=False,
        cascade="all, delete-orphan"
    )

    submissions = relationship(
        "Submission",
        back_populates="assignment",
        cascade="all, delete-orphan"
    )
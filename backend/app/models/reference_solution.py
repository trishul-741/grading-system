import uuid

from sqlalchemy import (
    Column,
    Text,
    DateTime,
    Enum,
    ForeignKey
)

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.enums import ProgrammingLanguage


class ReferenceSolution(Base):
    __tablename__ = "reference_solutions"

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
        unique=True
    )

    language = Column(
        Enum(
            ProgrammingLanguage,
            name="reference_language"
        ),
        nullable=False
    )

    source_code = Column(
        Text,
        nullable=False
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    assignment = relationship(
        "Assignment",
        back_populates="reference_solution"
    )
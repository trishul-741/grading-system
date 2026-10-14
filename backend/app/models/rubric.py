import uuid

from sqlalchemy import (
    Column,
    String,
    Text,
    Numeric,
    DateTime,
    Enum,
    ForeignKey
)

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.enums import EvaluationType


class Rubric(Base):
    __tablename__ = "rubrics"

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
        nullable=False
    )

    criterion = Column(
        String(100),
        nullable=False
    )

    description = Column(
        Text
    )

    max_marks = Column(
        Numeric(5, 2),
        nullable=False
    )

    weight = Column(
        Numeric(5, 2),
        nullable=False
    )

    evaluation_type = Column(
        Enum(
            EvaluationType,
            name="evaluation_type"
        ),
        nullable=False
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    assignment = relationship(
        "Assignment",
        back_populates="rubrics"
    )
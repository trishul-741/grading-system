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
from app.enums import (
    TestVisibility,
    TestSource,
    TestCategory,
    TestStatus
)


class TestCase(Base):
    __tablename__ = "test_cases"

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

    input = Column(
        Text,
        nullable=False
    )

    expected_output = Column(
        Text,
        nullable=False
    )

    visibility = Column(
        Enum(
            TestVisibility,
            name="test_visibility"
        ),
        nullable=False,
        default=TestVisibility.HIDDEN
    )

    source = Column(
        Enum(
            TestSource,
            name="test_source"
        ),
        nullable=False,
        default=TestSource.INSTRUCTOR
    )

    category = Column(
        Enum(
            TestCategory,
            name="test_category"
        ),
        nullable=False,
        default=TestCategory.NORMAL
    )

    status = Column(
        Enum(
            TestStatus,
            name="test_status"
        ),
        nullable=False,
        default=TestStatus.DRAFT
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    assignment = relationship(
        "Assignment",
        back_populates="test_cases"
    )
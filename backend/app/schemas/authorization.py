from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AssignmentTAMembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    assignment_id: UUID
    ta_id: UUID
    granted_by: UUID | None
    created_at: datetime

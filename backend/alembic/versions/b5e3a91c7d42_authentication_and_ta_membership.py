"""Add active accounts, revocable sessions and explicit TA membership.

Revision ID: b5e3a91c7d42
Revises: 9c2e1f7a4b60
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b5e3a91c7d42"
down_revision: str | Sequence[str] | None = "9c2e1f7a4b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Server default activates existing accounts without modifying credentials.
    # Legacy invalid hashes remain invalid until an operator explicitly resets.
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_auth_sessions_user", ondelete="CASCADE"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "assignment_ta_memberships",
        sa.Column("assignment_id", sa.UUID(), nullable=False),
        sa.Column("ta_id", sa.UUID(), nullable=False),
        sa.Column("granted_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("assignment_id", "ta_id"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], name="fk_assignment_ta_assignment", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ta_id"], ["users.id"], name="fk_assignment_ta_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"], name="fk_assignment_ta_grantor", ondelete="SET NULL"),
    )
    op.create_index("ix_assignment_ta_memberships_ta_id", "assignment_ta_memberships", ["ta_id"])


def downgrade() -> None:
    op.drop_index("ix_assignment_ta_memberships_ta_id", table_name="assignment_ta_memberships")
    op.drop_table("assignment_ta_memberships")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_column("users", "is_active")

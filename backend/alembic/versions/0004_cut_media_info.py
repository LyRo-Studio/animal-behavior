"""cut_media_info (ticket #22: Inspect probed media info for a Cut)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-15

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cut_media_info",
        sa.Column("s3_key", sa.String(length=1024), primary_key=True),
        sa.Column("etag", sa.String(length=255), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("codec", sa.String(length=100), nullable=False),
        sa.Column(
            "probed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("cut_media_info")

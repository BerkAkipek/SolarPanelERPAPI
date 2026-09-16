"""Create the production V1 database architecture."""

from pathlib import Path

from alembic import op

revision = "0001_v1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    schema = Path(__file__).resolve().parents[2] / "migrations" / "0001_v1.sql"
    op.execute(schema.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError(
        "V1 contains ERP history. Restore a backup or write an explicit reviewed migration."
    )

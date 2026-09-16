"""Protect reservation transitions, allocated demand, and movement direction."""

from pathlib import Path

from alembic import op

revision = "0002_invariants"
down_revision = "0001_v1"
branch_labels = None
depends_on = None


def upgrade():
    path = Path(__file__).resolve().parents[2] / "migrations" / "0002_invariants.sql"
    op.execute(path.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError(
        "Removing stock protections requires an explicit reviewed migration."
    )

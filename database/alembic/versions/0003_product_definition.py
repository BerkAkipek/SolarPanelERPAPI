"""Product definition: finished goods, active BOMs, and recipe invariants."""

from pathlib import Path

from alembic import op

revision = "0003_product_definition"
down_revision = "0002_invariants"
branch_labels = None
depends_on = None


def upgrade():
    path = Path(__file__).resolve().parents[2] / "migrations" / "0003_product_definition.sql"
    op.execute(path.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Product definition history requires an explicit reviewed migration.")

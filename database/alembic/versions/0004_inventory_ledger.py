"""Inventory ledger: explicit adjustments, physical on-hand, and retry protection."""
from pathlib import Path

from alembic import op

revision = "0004_inventory_ledger"
down_revision = "0003_product_definition"
branch_labels = None
depends_on = None


def upgrade():
    path = Path(__file__).resolve().parents[2] / "migrations" / "0004_inventory_ledger.sql"
    op.execute(path.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Inventory history requires an explicit reviewed migration.")

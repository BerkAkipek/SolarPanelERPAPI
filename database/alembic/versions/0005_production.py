"""Production order workflows: DRAFT state and material requirement snapshots."""
from pathlib import Path

from alembic import op

revision = "0005_production"
down_revision = "0004_inventory_ledger"
branch_labels = None
depends_on = None


def upgrade():
    path = Path(__file__).resolve().parents[2] / "migrations" / "0005_production.sql"
    op.execute(path.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Production history requires an explicit reviewed migration.")

"""Keep validation planning independent of historical JSON snapshot size."""
from importlib.resources import files

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'ef6a7b8c9d0e'
down_revision = 'de5f6a7b8c9d'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('daily_radar_candidates', sa.Column('validation_projection', JSONB(), nullable=True))
    op.add_column('daily_radar_candidates', sa.Column('validation_input_hash',
        sa.String(32), nullable=False, server_default='unrecorded'))
    op.get_bind().exec_driver_sql(files('ai_stock_sentinel.operations').joinpath(
        'radar_validation_projection_v1.sql').read_text())
    # One controlled backfill; future writes maintain the hash in their transaction.
    op.execute('UPDATE daily_radar_candidates SET validation_input_hash = validation_input_hash')


def downgrade():
    op.execute('DROP TRIGGER radar_validation_projection_v1 ON daily_radar_candidates')
    op.execute('DROP FUNCTION radar_validation_projection_v1()')
    op.drop_column('daily_radar_candidates', 'validation_input_hash')
    op.drop_column('daily_radar_candidates', 'validation_projection')

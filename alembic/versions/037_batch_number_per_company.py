"""batch number per company

Revision ID: 037_batch_number_per_company
Revises: 036_company_service_types
Create Date: 2026-10-22

route_batches.batch_number era único en toda la base, pero la secuencia se genera por
compañía (ORL-LOT-0001, 0002, ...). Con más de una compañía, el primer lote de la segunda
chocaba con el de la primera y fallaba con un error 500. Pasa a ser único por compañía.
"""

from alembic import op
import sqlalchemy as sa

revision      = '037_batch_number_per_company'
down_revision = '036_company_service_types'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE route_batches DROP CONSTRAINT IF EXISTS route_batches_batch_number_key"))
    conn.execute(sa.text(
        "ALTER TABLE route_batches ADD CONSTRAINT uq_route_batches_company_batch_number UNIQUE (company_id, batch_number)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE route_batches DROP CONSTRAINT IF EXISTS uq_route_batches_company_batch_number"))
    conn.execute(sa.text("ALTER TABLE route_batches ADD CONSTRAINT route_batches_batch_number_key UNIQUE (batch_number)"))

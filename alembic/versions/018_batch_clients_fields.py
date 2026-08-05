"""add address fields and codigo_cliente to batch_clients

Revision ID: 018_batch_clients_fields
Revises: 017_warehouse_inventory
Create Date: 2026-07-01

Agrega campos de dirección y código de cliente a batch_clients:
  - codigo_cliente VARCHAR(100) — mismo código usado en paradas
  - direccion      VARCHAR(500)
  - ciudad         VARCHAR(100)
  - estado         VARCHAR(50)
"""

from alembic import op
import sqlalchemy as sa

revision      = '018_batch_clients_fields'
down_revision = '017_warehouse_inventory'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE batch_clients
            ADD COLUMN IF NOT EXISTS codigo_cliente VARCHAR(100),
            ADD COLUMN IF NOT EXISTS direccion      VARCHAR(500),
            ADD COLUMN IF NOT EXISTS ciudad         VARCHAR(100),
            ADD COLUMN IF NOT EXISTS estado         VARCHAR(50)
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_batch_clients_codigo_cliente ON batch_clients(codigo_cliente) WHERE codigo_cliente IS NOT NULL"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE batch_clients
            DROP COLUMN IF EXISTS codigo_cliente,
            DROP COLUMN IF EXISTS direccion,
            DROP COLUMN IF EXISTS ciudad,
            DROP COLUMN IF EXISTS estado
    """))

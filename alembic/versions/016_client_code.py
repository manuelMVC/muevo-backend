"""add client_code to route_details, route_headers, route_batches

Revision ID: 016_client_code
Revises: 015_batch_clients
Create Date: 2026-07-01

Agrega el campo codigo_cliente por parada y propaga automáticamente
el client_code a nivel de ruta y lote cuando todas las paradas/rutas
pertenecen al mismo cliente.

Cambios:
  - route_details:  codigo_cliente VARCHAR(100)
  - route_headers:  client_code    VARCHAR(100)
  - route_batches:  client_code    VARCHAR(100)
"""

from alembic import op
import sqlalchemy as sa

revision      = '016_client_code'
down_revision = '015_batch_clients'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        ALTER TABLE route_details
            ADD COLUMN IF NOT EXISTS codigo_cliente VARCHAR(100)
    """))

    conn.execute(sa.text("""
        ALTER TABLE route_headers
            ADD COLUMN IF NOT EXISTS client_code VARCHAR(100)
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_route_headers_client_code ON route_headers(client_code) WHERE client_code IS NOT NULL"
    ))

    conn.execute(sa.text("""
        ALTER TABLE route_batches
            ADD COLUMN IF NOT EXISTS client_code VARCHAR(100)
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_route_batches_client_code ON route_batches(client_code) WHERE client_code IS NOT NULL"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE route_details  DROP COLUMN IF EXISTS codigo_cliente"))
    conn.execute(sa.text("ALTER TABLE route_headers  DROP COLUMN IF EXISTS client_code"))
    conn.execute(sa.text("ALTER TABLE route_batches  DROP COLUMN IF EXISTS client_code"))

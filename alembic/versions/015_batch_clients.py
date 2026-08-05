"""add batch_clients table and client_id FK in route_batches

Revision ID: 015_batch_clients
Revises: 014_profiles_permissions
Create Date: 2026-07-01

Agrega la tabla batch_clients para contactos externos que reciben
notificaciones de cambio de estado de lotes y paradas.

Cambios:
  - Nueva tabla batch_clients con preferencias de notificación por evento
  - FK client_id en route_batches (nullable — el lote puede no tener cliente)
"""

from alembic import op
import sqlalchemy as sa

revision      = '015_batch_clients'
down_revision = '014_profiles_permissions'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Crear tabla batch_clients ──────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS batch_clients (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id           UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            name                 VARCHAR(255) NOT NULL,
            email                VARCHAR(255) NOT NULL,
            phone                VARCHAR(30),
            push_token           VARCHAR(255),
            notify_batch_status  BOOLEAN NOT NULL DEFAULT TRUE,
            notify_stop_status   BOOLEAN NOT NULL DEFAULT FALSE,
            notify_delivery      BOOLEAN NOT NULL DEFAULT TRUE,
            notify_incident      BOOLEAN NOT NULL DEFAULT TRUE,
            is_active            BOOLEAN NOT NULL DEFAULT TRUE,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_batch_clients_company_id ON batch_clients(company_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_batch_clients_email ON batch_clients(email)"
    ))

    # ── 2. Agregar client_id FK en route_batches ──────────────────────────────
    conn.execute(sa.text("""
        ALTER TABLE route_batches
            ADD COLUMN IF NOT EXISTS client_id UUID REFERENCES batch_clients(id) ON DELETE SET NULL
    """))

    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_route_batches_client_id ON route_batches(client_id) WHERE client_id IS NOT NULL"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE route_batches DROP COLUMN IF EXISTS client_id"))
    conn.execute(sa.text("DROP TABLE IF EXISTS batch_clients CASCADE"))

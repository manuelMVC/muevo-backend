"""multi-transporter negotiation with holding approval

Revision ID: 028_multi_carrier_negotiation
Revises: 027_batch_cancellation_reason
Create Date: 2026-08-31

Extiende el sistema de negociación de precios (022_price_negotiation) para
soportar marketplace abierto (varios transportistas pujando en paralelo por
la misma ruta) y un paso de aprobación a nivel holding antes de cerrar el
trato:
  - route_price_offers.transport_company_id: hilo propio de cada transportista
    (NULL = oferta pública/broadcast del warehouse)
  - offerstatus: nuevo valor 'selected' (elegida por el warehouse, en holding)
  - negotiationstatus: nuevo valor 'pending_holding_approval'
  - route_headers: pending_offer_id, holding_approved_by, holding_approved_at
"""

from alembic import op
import sqlalchemy as sa

revision      = '028_multi_carrier_negotiation'
down_revision = '027_batch_cancellation_reason'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Nuevos valores de enum ────────────────────────────────────────────────
    conn.execute(sa.text(
        "ALTER TYPE offerstatus ADD VALUE IF NOT EXISTS 'selected' AFTER 'pending'"
    ))
    conn.execute(sa.text(
        "ALTER TYPE negotiationstatus ADD VALUE IF NOT EXISTS 'pending_holding_approval' AFTER 'countered'"
    ))

    # ── route_price_offers: hilo por transportista ────────────────────────────
    conn.execute(sa.text("""
        ALTER TABLE route_price_offers
            ADD COLUMN IF NOT EXISTS transport_company_id UUID REFERENCES transport_companies(id)
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_rpo_transport_company_id ON route_price_offers(transport_company_id)"
    ))

    # ── route_headers: seguimiento de aprobación de holding ──────────────────
    conn.execute(sa.text("""
        ALTER TABLE route_headers
            ADD COLUMN IF NOT EXISTS pending_offer_id UUID REFERENCES route_price_offers(id),
            ADD COLUMN IF NOT EXISTS holding_approved_by UUID REFERENCES users(id),
            ADD COLUMN IF NOT EXISTS holding_approved_at TIMESTAMPTZ
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE route_headers DROP COLUMN IF EXISTS pending_offer_id"))
    conn.execute(sa.text("ALTER TABLE route_headers DROP COLUMN IF EXISTS holding_approved_by"))
    conn.execute(sa.text("ALTER TABLE route_headers DROP COLUMN IF EXISTS holding_approved_at"))
    conn.execute(sa.text("ALTER TABLE route_price_offers DROP COLUMN IF EXISTS transport_company_id"))
    # PostgreSQL no soporta DROP VALUE de un enum directamente — downgrade no-op
    # para los nuevos valores de offerstatus/negotiationstatus (adición no destructiva).

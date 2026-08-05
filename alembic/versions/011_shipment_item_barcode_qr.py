"""add barcode and qr_code to shipment_items

Revision ID: 011_shipment_item_barcode_qr
Revises: 010_remove_offered_batchstatus
Create Date: 2026-07-01

Agrega dos columnas opcionales a shipment_items:
  - barcode  VARCHAR(200): código de barras del paquete
  - qr_code  VARCHAR(500): contenido del código QR (URL, texto, etc.)

Ambas son nullable — no todos los paquetes los tienen.
"""

from alembic import op
import sqlalchemy as sa

revision      = '011_shipment_item_barcode_qr'
down_revision = '010_remove_offered_batchstatus'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE shipment_items
            ADD COLUMN IF NOT EXISTS barcode  VARCHAR(200),
            ADD COLUMN IF NOT EXISTS qr_code  VARCHAR(500)
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_shipment_items_barcode ON shipment_items(barcode) WHERE barcode IS NOT NULL"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_shipment_items_barcode"))
    conn.execute(sa.text("""
        ALTER TABLE shipment_items
            DROP COLUMN IF EXISTS barcode,
            DROP COLUMN IF EXISTS qr_code
    """))

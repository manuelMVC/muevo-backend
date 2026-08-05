"""add warehouse_inventory_items table

Revision ID: 017_warehouse_inventory
Revises: 016_client_code
Create Date: 2026-07-01

Tabla de inventario para el flujo de escaneo de cajas en el warehouse.
Soporta:
  - Flujo A: inventario previo → generar lote
  - Flujo B: escaneo directo → agregar a lote en curso
  - Agrupación automática de múltiples cajas con el mismo código
"""

from alembic import op
import sqlalchemy as sa

revision      = '017_warehouse_inventory'
down_revision = '016_client_code'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    # Enum de estado
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE inventoryitemstatus AS ENUM (
                'pending', 'added_to_batch', 'cancelled'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS warehouse_inventory_items (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id        UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            raw_code          VARCHAR(500) NOT NULL,
            codigo_cliente    VARCHAR(100),
            destino           VARCHAR(255),
            direccion         VARCHAR(500),
            lat               FLOAT,
            lng               FLOAT,
            peso_lbs_unit     NUMERIC(10,2) NOT NULL DEFAULT 0,
            volumen_ft3_unit  NUMERIC(10,2) NOT NULL DEFAULT 0,
            contacto          VARCHAR(255),
            telefono          VARCHAR(30),
            notas             TEXT,
            quantity          INTEGER NOT NULL DEFAULT 1,
            peso_lbs_total    NUMERIC(10,2) NOT NULL DEFAULT 0,
            volumen_ft3_total NUMERIC(10,2) NOT NULL DEFAULT 0,
            status            inventoryitemstatus NOT NULL DEFAULT 'pending',
            batch_id          UUID REFERENCES route_batches(id) ON DELETE SET NULL,
            scanned_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            scanned_by_id     UUID REFERENCES users(id)
        )
    """))

    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS ix_wii_company_id          ON warehouse_inventory_items(company_id)",
        "CREATE INDEX IF NOT EXISTS ix_wii_raw_code            ON warehouse_inventory_items(raw_code)",
        "CREATE INDEX IF NOT EXISTS ix_wii_status              ON warehouse_inventory_items(status)",
        "CREATE INDEX IF NOT EXISTS ix_wii_batch_id            ON warehouse_inventory_items(batch_id) WHERE batch_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_wii_codigo_cliente      ON warehouse_inventory_items(codigo_cliente) WHERE codigo_cliente IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_wii_company_code_status ON warehouse_inventory_items(company_id, raw_code, status)",
    ]:
        conn.execute(sa.text(idx_sql))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS warehouse_inventory_items CASCADE"))
    conn.execute(sa.text("DROP TYPE IF EXISTS inventoryitemstatus"))

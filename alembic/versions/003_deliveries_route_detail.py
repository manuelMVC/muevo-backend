"""add route_detail_id to deliveries, make stop_id nullable

Revision ID: 003_deliveries_route_detail
Revises: 002_holdings_warehouses
Create Date: 2026-06-22

Permite que las confirmaciones de entrega (PoD) se vinculen tanto al
modelo legacy (stops) como al modelo vigente (route_details), sin
romper datos existentes.
"""

from alembic import op
import sqlalchemy as sa

revision = '003_deliveries_route_detail'
down_revision = '002_holdings_warehouses'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. stop_id pasa a ser opcional (el modelo vigente no siempre usa 'stops')
    conn.execute(sa.text("ALTER TABLE deliveries ALTER COLUMN stop_id DROP NOT NULL"))

    # 2. Quitar el UNIQUE constraint sobre stop_id si existe, para poder
    #    tener filas con stop_id NULL sin conflicto
    conn.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'deliveries_stop_id_key'
            ) THEN
                ALTER TABLE deliveries DROP CONSTRAINT deliveries_stop_id_key;
            END IF;
        END $$;
    """))

    # 3. Agregar route_detail_id (la referencia vigente para el modelo
    #    Holding -> Company -> Warehouse -> RouteHeader -> RouteDetail)
    conn.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='deliveries' AND column_name='route_detail_id') THEN
                ALTER TABLE deliveries ADD COLUMN route_detail_id UUID
                    REFERENCES route_details(id) ON DELETE CASCADE;
            END IF;
        END $$;
    """))

    conn.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_deliveries_route_detail_id
        ON deliveries(route_detail_id) WHERE route_detail_id IS NOT NULL
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_deliveries_route_detail_id ON deliveries(route_detail_id)"
    ))

    # 4. Constraint: cada delivery debe apuntar a stop_id O route_detail_id (no ambos vacíos)
    conn.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_delivery_has_target'
            ) THEN
                ALTER TABLE deliveries ADD CONSTRAINT ck_delivery_has_target
                    CHECK (stop_id IS NOT NULL OR route_detail_id IS NOT NULL);
            END IF;
        END $$;
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE deliveries DROP CONSTRAINT IF EXISTS ck_delivery_has_target"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_deliveries_route_detail_id"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ux_deliveries_route_detail_id"))
    conn.execute(sa.text("ALTER TABLE deliveries DROP COLUMN IF EXISTS route_detail_id"))
    conn.execute(sa.text("ALTER TABLE deliveries ALTER COLUMN stop_id SET NOT NULL"))

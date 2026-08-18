"""add receptions + reception_items tables (proceso de recepción / inbound)

Revision ID: 025_receptions
Revises: 024_company_csv_limits
Create Date: 2026-08-15

Modela la recepción de mercancía entrante al warehouse — tanto mercancía
nueva de proveedores/clientes como devoluciones de rutas ya despachadas
(mismo flujo unificado). Cada Reception es un manifiesto esperado con N
ReceptionItem; el check-in físico marca cada ítem como received/damaged,
y solo los received generan un WarehouseInventoryItem (inventario
disponible para armar lotes). Al cerrar la recepción, cualquier ítem que
siga pending pasa a missing.
"""

from alembic import op
import sqlalchemy as sa

revision      = '025_receptions'
down_revision = '024_company_csv_limits'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE receptionsourcetype AS ENUM ('supplier', 'return');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE receptionstatus AS ENUM ('expected', 'in_progress', 'completed');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE receptionitemstatus AS ENUM ('pending', 'received', 'damaged', 'missing');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS receptions (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id        UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            reception_number  VARCHAR(50) UNIQUE,
            source_type       receptionsourcetype NOT NULL,
            reference         VARCHAR(255),
            origin_route_id   UUID REFERENCES route_headers(id),
            status            receptionstatus NOT NULL DEFAULT 'expected',
            expected_at       TIMESTAMPTZ,
            received_at       TIMESTAMPTZ,
            notes             TEXT,
            created_by_id     UUID REFERENCES users(id),
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS reception_items (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            reception_id       UUID NOT NULL REFERENCES receptions(id) ON DELETE CASCADE,
            codigo_cliente     VARCHAR(100),
            descripcion        VARCHAR(255),
            expected_quantity  INTEGER NOT NULL DEFAULT 1,
            received_quantity  INTEGER NOT NULL DEFAULT 0,
            peso_lbs_unit      NUMERIC(10,2) NOT NULL DEFAULT 0,
            volumen_ft3_unit   NUMERIC(10,2) NOT NULL DEFAULT 0,
            status             receptionitemstatus NOT NULL DEFAULT 'pending',
            notas              TEXT,
            inventory_item_id  UUID REFERENCES warehouse_inventory_items(id),
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS ix_receptions_company_id      ON receptions(company_id)",
        "CREATE INDEX IF NOT EXISTS ix_receptions_status          ON receptions(status)",
        "CREATE INDEX IF NOT EXISTS ix_receptions_source_type     ON receptions(source_type)",
        "CREATE INDEX IF NOT EXISTS ix_reception_items_reception_id ON reception_items(reception_id)",
        "CREATE INDEX IF NOT EXISTS ix_reception_items_status       ON reception_items(status)",
    ]:
        conn.execute(sa.text(idx_sql))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS reception_items CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS receptions CASCADE"))
    conn.execute(sa.text("DROP TYPE IF EXISTS receptionitemstatus"))
    conn.execute(sa.text("DROP TYPE IF EXISTS receptionstatus"))
    conn.execute(sa.text("DROP TYPE IF EXISTS receptionsourcetype"))

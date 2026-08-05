"""add service_types table and FK in route_headers

Revision ID: 012_service_types
Revises: 011_shipment_item_barcode_qr
Create Date: 2026-07-01

Crea la tabla service_types con sus campos financieros y de configuración,
agrega FK service_type_id en route_headers, y siembra los 4 tipos base.
"""

from alembic import op
import sqlalchemy as sa

revision      = '012_service_types'
down_revision = '011_shipment_item_barcode_qr'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Crear tabla service_types ─────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS service_types (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code             VARCHAR(60)    NOT NULL UNIQUE,
            name             VARCHAR(120)   NOT NULL,
            description      TEXT,
            porcentaje_muevo NUMERIC(5,2)   NOT NULL DEFAULT 12.00,
            importe_minimo   NUMERIC(10,2)  NOT NULL DEFAULT 0.00,
            importe_maximo   NUMERIC(10,2),
            precio_servicio  NUMERIC(10,2)  NOT NULL DEFAULT 0.00,
            habilitado       BOOLEAN        NOT NULL DEFAULT TRUE,
            created_at       TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ    NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_service_types_code ON service_types(code)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_service_types_habilitado ON service_types(habilitado)"
    ))

    # ── 2. Sembrar los 4 tipos base ──────────────────────────────────────────
    conn.execute(sa.text("""
        INSERT INTO service_types
            (code, name, description, porcentaje_muevo, importe_minimo, importe_maximo, precio_servicio, habilitado)
        VALUES
            ('mensajeria', 'Mensajería',
             'Entrega de sobres, documentos y paquetes pequeños con firma de recepción.',
             12.00, 15.00, 500.00, 25.00, TRUE),
            ('logistica',  'Logística',
             'Transporte y distribución de carga en palés, cajas y contenedores.',
             10.00, 50.00, 5000.00, 80.00, TRUE),
            ('paqueteria', 'Paquetería',
             'Envío de paquetes de tamaño mediano con seguimiento y confirmación de entrega.',
             11.00, 20.00, 1000.00, 35.00, TRUE),
            ('personas',   'Personas',
             'Transporte de empleados, ejecutivos o grupos de personas.',
             12.00, 30.00, 2000.00, 45.00, TRUE)
        ON CONFLICT (code) DO NOTHING
    """))

    # ── 3. Agregar FK service_type_id en route_headers ───────────────────────
    conn.execute(sa.text("""
        ALTER TABLE route_headers
            ADD COLUMN IF NOT EXISTS service_type_id UUID REFERENCES service_types(id)
    """))

    # ── 4. Poblar service_type_id desde service_mode existente ───────────────
    conn.execute(sa.text("""
        UPDATE route_headers rh
        SET service_type_id = st.id
        FROM service_types st
        WHERE rh.service_mode::text = st.code
          AND rh.service_type_id IS NULL
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "ALTER TABLE route_headers DROP COLUMN IF EXISTS service_type_id"
    ))
    conn.execute(sa.text("DROP TABLE IF EXISTS service_types CASCADE"))

"""add route_batches, route_batch_items, incident_types, incidents

Revision ID: 007_batches_and_incidents
Revises: 006_transport_company_admins
Create Date: 2026-07-01

Implementa:
- Tabla incident_types  — catálogo configurable de tipos de incidencia
- Tabla incidents       — incidencias reportadas por conductores, admins TC, o admins warehouse
- Tabla route_batches   — lotes de rutas ofrecidos a empresas de transporte
- Tabla route_batch_items — ítems individuales dentro de un lote (accept/reject/reassign)
- Nuevo valor 'incident_reported' en el enum route_status
- Seed inicial de incident_types con los 7 tipos base
"""

from alembic import op
import sqlalchemy as sa

revision      = '007_batches_and_incidents'
down_revision = '006_transport_company_admins'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. New enum values ────────────────────────────────────────────────────
    # Add incident_reported to route_status enum (PostgreSQL requires ALTER TYPE)
    conn.execute(sa.text("ALTER TYPE routestatus ADD VALUE IF NOT EXISTS 'incident_reported'"))

    # New enums
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE batchstatus AS ENUM (
                'draft','approved','partially_accepted','fully_accepted',
                'partially_completed','completed','closed_with_incidents',
                'cancelled','expired'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE batchitemstatus AS ENUM (
                'pending','accepted','rejected','reassigned',
                'completed','closed_with_incidents'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE incidentstatus AS ENUM (
                'open','under_review','resolved','unresolved'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE incidentseverity AS ENUM (
                'low','medium','high','critical'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE reporterrole AS ENUM (
                'driver','transport_admin','warehouse_admin'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """))

    # ── 2. incident_types ─────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS incident_types (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code                  VARCHAR(60)  NOT NULL UNIQUE,
            name                  VARCHAR(120) NOT NULL,
            description           TEXT,
            severity              incidentseverity NOT NULL DEFAULT 'medium',
            affects_route_status  BOOLEAN NOT NULL DEFAULT FALSE,
            is_active             BOOLEAN NOT NULL DEFAULT TRUE,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    # ── 3. incidents ──────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS incidents (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            incident_type_id      UUID NOT NULL REFERENCES incident_types(id),
            reported_by_user_id   UUID NOT NULL REFERENCES users(id),
            reporter_role         reporterrole NOT NULL,

            route_header_id       UUID REFERENCES route_headers(id),
            route_detail_id       UUID REFERENCES route_details(id),
            batch_item_id         UUID,   -- FK added after route_batch_items is created

            status                incidentstatus NOT NULL DEFAULT 'open',
            title                 VARCHAR(200) NOT NULL,
            description           TEXT NOT NULL,
            evidence_urls         JSONB NOT NULL DEFAULT '[]',

            resolved_by_user_id   UUID REFERENCES users(id),
            resolution_notes      TEXT,
            resolved_at           TIMESTAMPTZ,

            reported_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_incidents_route_header_id ON incidents(route_header_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_incidents_status          ON incidents(status)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_incidents_reported_by     ON incidents(reported_by_user_id)"))

    # ── 4. route_batches ──────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS route_batches (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id    UUID NOT NULL REFERENCES companies(id),
            contract_id   UUID REFERENCES contracts(id),
            status        batchstatus NOT NULL DEFAULT 'draft',
            notes         TEXT,
            offered_at    TIMESTAMPTZ,
            expires_at    TIMESTAMPTZ,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_route_batches_company_id ON route_batches(company_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_route_batches_status     ON route_batches(status)"))

    # ── 5. route_batch_items ──────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS route_batch_items (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            batch_id              UUID NOT NULL REFERENCES route_batches(id) ON DELETE CASCADE,
            route_header_id       UUID NOT NULL REFERENCES route_headers(id),
            transport_company_id  UUID REFERENCES transport_companies(id),
            status                batchitemstatus NOT NULL DEFAULT 'pending',
            assigned_at           TIMESTAMPTZ,
            incident_id           UUID REFERENCES incidents(id),
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(batch_id, route_header_id)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_batch_items_batch_id             ON route_batch_items(batch_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_batch_items_route_header_id      ON route_batch_items(route_header_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_batch_items_transport_company_id ON route_batch_items(transport_company_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_batch_items_status               ON route_batch_items(status)"))

    # ── 6. Close the FK loop: incidents.batch_item_id → route_batch_items ────
    conn.execute(sa.text("""
        ALTER TABLE incidents
            ADD CONSTRAINT fk_incidents_batch_item_id
            FOREIGN KEY (batch_item_id) REFERENCES route_batch_items(id)
    """))

    # ── 7. Seed incident_types ────────────────────────────────────────────────
    conn.execute(sa.text("""
        INSERT INTO incident_types (code, name, description, severity, affects_route_status) VALUES
        ('vehicle_accident',     'Accidente de vehículo',             'Colisión u otro accidente vehicular durante la ruta.',                             'critical', TRUE),
        ('damaged_package',      'Paquete dañado o faltante',         'Un paquete fue dañado en tránsito o no se encuentra en la carga.',                 'high',     FALSE),
        ('wrong_address',        'Dirección incorrecta o inaccesible','La dirección del destinatario es incorrecta o el conductor no puede acceder.',       'medium',   FALSE),
        ('absent_recipient',     'Destinatario ausente',              'El destinatario no se encontraba presente en el momento de la entrega.',             'low',      FALSE),
        ('traffic_delay',        'Demora por tráfico o clima',        'La ruta presenta demoras significativas por condiciones de tráfico o meteorológicas.','low',      FALSE),
        ('delivery_rejected',    'Rechazo de entrega por destinatario','El destinatario se negó a recibir el paquete.',                                   'medium',   FALSE),
        ('mechanical_failure',   'Problema mecánico del vehículo',    'El vehículo sufrió una falla mecánica que impide continuar la ruta.',               'high',     TRUE)
        ON CONFLICT (code) DO NOTHING
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE incidents DROP CONSTRAINT IF EXISTS fk_incidents_batch_item_id"))
    conn.execute(sa.text("DROP TABLE IF EXISTS route_batch_items CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS route_batches CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS incidents CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS incident_types CASCADE"))
    conn.execute(sa.text("DROP TYPE IF EXISTS batchitemstatus CASCADE"))
    conn.execute(sa.text("DROP TYPE IF EXISTS batchstatus CASCADE"))
    conn.execute(sa.text("DROP TYPE IF EXISTS incidentstatus CASCADE"))
    conn.execute(sa.text("DROP TYPE IF EXISTS incidentseverity CASCADE"))
    conn.execute(sa.text("DROP TYPE IF EXISTS reporterrole CASCADE"))

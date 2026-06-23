"""add holdings, warehouses, route headers/details, transport companies

Revision ID: 002_holdings_warehouses
Revises: 001_initial
Create Date: 2026-06-21

Nota: usa SQL puro (igual que 001_initial) para evitar conflictos
con los ENUMs de SQLAlchemy al correr en PostgreSQL.
"""

from alembic import op
import sqlalchemy as sa

revision = '002_holdings_warehouses'
down_revision = '001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── HOLDINGS ──────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS holdings (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name                   VARCHAR(255) NOT NULL,
            legal_name             VARCHAR(255),
            tax_id                 VARCHAR(50)  UNIQUE,
            holding_type           VARCHAR(30)  NOT NULL DEFAULT 'single_entity',
            billing_mode           VARCHAR(20)  NOT NULL DEFAULT 'per_company',
            billing_email          VARCHAR(255),
            payment_terms_days     INTEGER      NOT NULL DEFAULT 30,
            stripe_customer_id     VARCHAR(255),
            credit_limit           NUMERIC(14,2) NOT NULL DEFAULT 0.00,
            primary_contact_name   VARCHAR(255),
            primary_contact_email  VARCHAR(255),
            primary_contact_phone  VARCHAR(30),
            default_allowed_modes  TEXT[]       NOT NULL DEFAULT '{}',
            default_required_certs TEXT[]       NOT NULL DEFAULT '{}',
            industry               VARCHAR(100),
            country                VARCHAR(50)  NOT NULL DEFAULT 'US',
            is_active              BOOLEAN      NOT NULL DEFAULT TRUE,
            logo_url               VARCHAR(512),
            notes                  TEXT,
            created_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_holdings_tax_id ON holdings(tax_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_holdings_type    ON holdings(holding_type)"))

    # ── HOLDING_USERS ─────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS holding_users (
            id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            holding_id                      UUID NOT NULL REFERENCES holdings(id) ON DELETE CASCADE,
            user_id                         UUID NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
            role                            VARCHAR(30) NOT NULL DEFAULT 'executive_viewer',
            can_create_companies            BOOLEAN NOT NULL DEFAULT FALSE,
            can_view_consolidated_billing   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(holding_id, user_id)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_holding_users_holding ON holding_users(holding_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_holding_users_user     ON holding_users(user_id)"))

    # ── COMPANIES: agregar columnas de jerarquía holding ──────────────────────
    conn.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='companies' AND column_name='holding_id') THEN
                ALTER TABLE companies ADD COLUMN holding_id UUID REFERENCES holdings(id) ON DELETE CASCADE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='companies' AND column_name='is_primary_company') THEN
                ALTER TABLE companies ADD COLUMN is_primary_company BOOLEAN NOT NULL DEFAULT TRUE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='companies' AND column_name='billing_mode') THEN
                ALTER TABLE companies ADD COLUMN billing_mode VARCHAR(20) NOT NULL DEFAULT 'inherit';
            END IF;
        END $$;
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_companies_holding_id ON companies(holding_id)"))

    # ── WAREHOUSES ────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS warehouses (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id            UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            code                  VARCHAR(30)  NOT NULL,
            name                  VARCHAR(255) NOT NULL,
            warehouse_type        VARCHAR(50)  NOT NULL DEFAULT 'distribution',
            address_line1         VARCHAR(255) NOT NULL,
            address_line2         VARCHAR(255),
            city                  VARCHAR(100) NOT NULL,
            state                 VARCHAR(50)  NOT NULL,
            zip_code              VARCHAR(20),
            country               VARCHAR(50)  NOT NULL DEFAULT 'US',
            lat                   FLOAT,
            lng                   FLOAT,
            contact_name          VARCHAR(255),
            contact_phone         VARCHAR(30),
            contact_email         VARCHAR(255),
            docks_count           INTEGER      NOT NULL DEFAULT 1,
            operating_hours       JSONB,
            default_wait_minutes  INTEGER      NOT NULL DEFAULT 15,
            timezone              VARCHAR(64)  NOT NULL DEFAULT 'America/New_York',
            is_origin_default     BOOLEAN      NOT NULL DEFAULT FALSE,
            requires_appointment  BOOLEAN      NOT NULL DEFAULT FALSE,
            is_active             BOOLEAN      NOT NULL DEFAULT TRUE,
            notes                 TEXT,
            created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            UNIQUE(company_id, code)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_warehouses_company_id ON warehouses(company_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_warehouses_city_state  ON warehouses(city, state)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_warehouses_location     ON warehouses(lat, lng)"))

    # ── WAREHOUSE_USERS ───────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS warehouse_users (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            warehouse_id       UUID NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
            user_id            UUID NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
            role               VARCHAR(30) NOT NULL DEFAULT 'operator',
            can_create_routes  BOOLEAN NOT NULL DEFAULT TRUE,
            can_approve_pod    BOOLEAN NOT NULL DEFAULT FALSE,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(warehouse_id, user_id)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_warehouse_users_wh   ON warehouse_users(warehouse_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_warehouse_users_user ON warehouse_users(user_id)"))

    # ── TRANSPORT_COMPANIES ───────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS transport_companies (
            id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name                      VARCHAR(255) NOT NULL,
            legal_name                VARCHAR(255),
            tax_id                    VARCHAR(50)  UNIQUE,
            dot_number                VARCHAR(30),
            mc_number                 VARCHAR(30),
            insurance_policy_number   VARCHAR(100),
            insurance_expires         TIMESTAMPTZ,
            is_verified               BOOLEAN      NOT NULL DEFAULT FALSE,
            verified_at               TIMESTAMPTZ,
            verified_by               UUID REFERENCES users(id),
            contact_name              VARCHAR(255),
            contact_email             VARCHAR(255),
            contact_phone             VARCHAR(30),
            address_line1             VARCHAR(255),
            city                      VARCHAR(100),
            state                     VARCHAR(50),
            zip_code                  VARCHAR(20),
            base_lat                  FLOAT,
            base_lng                  FLOAT,
            is_active                 BOOLEAN      NOT NULL DEFAULT TRUE,
            service_modes             TEXT[]       NOT NULL DEFAULT '{}',
            total_routes              INTEGER      NOT NULL DEFAULT 0,
            completed_routes          INTEGER      NOT NULL DEFAULT 0,
            rejected_routes           INTEGER      NOT NULL DEFAULT 0,
            avg_rating                NUMERIC(3,2) NOT NULL DEFAULT 5.00,
            on_time_pct               NUMERIC(5,2) NOT NULL DEFAULT 100.00,
            total_earnings_net        NUMERIC(14,2) NOT NULL DEFAULT 0.00,
            stripe_account_id         VARCHAR(255),
            stripe_onboarded          BOOLEAN      NOT NULL DEFAULT FALSE,
            notes                     TEXT,
            created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_tc_is_active   ON transport_companies(is_active)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_tc_is_verified ON transport_companies(is_verified)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_tc_location     ON transport_companies(base_lat, base_lng)"))

    # ── VEHICLES: agregar transport_company_id, hacer driver_id nullable ────
    conn.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='vehicles' AND column_name='transport_company_id') THEN
                ALTER TABLE vehicles ADD COLUMN transport_company_id UUID
                    REFERENCES transport_companies(id) ON DELETE CASCADE;
            END IF;
        END $$;
    """))
    conn.execute(sa.text("ALTER TABLE vehicles ALTER COLUMN driver_id DROP NOT NULL"))

    # ── ROUTE_HEADERS ─────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS route_headers (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id               UUID NOT NULL REFERENCES companies(id),
            origin_warehouse_id      UUID REFERENCES warehouses(id),
            contract_id              UUID REFERENCES contracts(id),
            transport_company_id     UUID REFERENCES transport_companies(id),
            vehicle_id               UUID REFERENCES vehicles(id),
            dispatched_by            UUID REFERENCES users(id),
            dispatch_mode            VARCHAR(20)  NOT NULL DEFAULT 'manual',
            route_number             VARCHAR(50)  NOT NULL UNIQUE,
            external_reference       VARCHAR(100),
            source                   VARCHAR(20)  NOT NULL DEFAULT 'manual',
            title                    VARCHAR(255) NOT NULL,
            service_mode             servicemode  NOT NULL,
            status                   routestatus  NOT NULL DEFAULT 'draft',
            scheduled_date           TIMESTAMPTZ  NOT NULL,
            scheduled_start          TIMESTAMPTZ  NOT NULL,
            scheduled_end            TIMESTAMPTZ  NOT NULL,
            actual_start             TIMESTAMPTZ,
            actual_end               TIMESTAMPTZ,
            required_vehicle_type    vehicletype,
            required_certs           TEXT[]       NOT NULL DEFAULT '{}',
            total_stops              INTEGER      NOT NULL DEFAULT 0,
            completed_stops          INTEGER      NOT NULL DEFAULT 0,
            total_km_estimated       NUMERIC(8,2),
            total_km_actual          NUMERIC(8,2),
            total_packages           INTEGER      NOT NULL DEFAULT 0,
            total_weight_lbs         NUMERIC(10,2) NOT NULL DEFAULT 0,
            total_volume_ft3         NUMERIC(10,2) NOT NULL DEFAULT 0,
            total_cargo_value        NUMERIC(12,2) NOT NULL DEFAULT 0,
            has_fragile_items        BOOLEAN      NOT NULL DEFAULT FALSE,
            has_hazmat_items         BOOLEAN      NOT NULL DEFAULT FALSE,
            requires_temp_control    BOOLEAN      NOT NULL DEFAULT FALSE,
            requires_insurance       BOOLEAN      NOT NULL DEFAULT FALSE,
            stackable                BOOLEAN      NOT NULL DEFAULT TRUE,
            gross_pay                NUMERIC(10,2) NOT NULL,
            muevo_commission_pct     NUMERIC(5,2)  NOT NULL DEFAULT 12.00,
            muevo_commission_amt     NUMERIC(10,2) NOT NULL,
            fuel_cost_estimated      NUMERIC(8,2),
            wear_cost_estimated      NUMERIC(8,2),
            net_pay_estimated        NUMERIC(10,2),
            net_pay_actual           NUMERIC(10,2),
            margin_pct               NUMERIC(5,2),
            sla_max_delay_minutes    INTEGER      NOT NULL DEFAULT 15,
            sla_penalty              NUMERIC(8,2)  NOT NULL DEFAULT 0.00,
            sla_bonus                NUMERIC(8,2)  NOT NULL DEFAULT 0.00,
            on_time_pct              NUMERIC(5,2),
            optimized_order          JSONB,
            driver_notes             TEXT,
            internal_notes           TEXT,
            loading_instructions     JSONB,
            passenger_manifest       JSONB,
            created_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_rh_company_id     ON route_headers(company_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_rh_warehouse_id    ON route_headers(origin_warehouse_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_rh_transport_co     ON route_headers(transport_company_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_rh_status           ON route_headers(status)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_rh_scheduled_date    ON route_headers(scheduled_date)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_rh_route_number      ON route_headers(route_number)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_rh_external_ref       ON route_headers(external_reference)"))

    # ── ROUTE_DETAILS ─────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS route_details (
            id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            route_header_id           UUID NOT NULL REFERENCES route_headers(id) ON DELETE CASCADE,
            destination_warehouse_id  UUID REFERENCES warehouses(id),
            sequence_order            INTEGER      NOT NULL,
            original_order            INTEGER,
            status                    stopstatus   NOT NULL DEFAULT 'pending',
            address_line1             VARCHAR(255) NOT NULL,
            address_line2             VARCHAR(255),
            city                      VARCHAR(100) NOT NULL,
            state                     VARCHAR(50)  NOT NULL,
            zip_code                  VARCHAR(20),
            lat                       FLOAT,
            lng                       FLOAT,
            company_name              VARCHAR(255),
            contact_name              VARCHAR(255),
            contact_phone             VARCHAR(30),
            contact_email             VARCHAR(255),
            floor_suite               VARCHAR(100),
            access_notes              TEXT,
            packages_count            INTEGER       NOT NULL DEFAULT 0,
            weight_lbs                NUMERIC(10,2) NOT NULL DEFAULT 0,
            volume_ft3                NUMERIC(10,2) NOT NULL DEFAULT 0,
            cargo_value               NUMERIC(12,2) NOT NULL DEFAULT 0,
            is_fragile                BOOLEAN       NOT NULL DEFAULT FALSE,
            is_hazmat                 BOOLEAN       NOT NULL DEFAULT FALSE,
            is_temp_controlled        BOOLEAN       NOT NULL DEFAULT FALSE,
            is_stackable              BOOLEAN       NOT NULL DEFAULT TRUE,
            requires_insurance        BOOLEAN       NOT NULL DEFAULT FALSE,
            dock_number               VARCHAR(50),
            loading_notes             TEXT,
            wait_minutes_estimated    INTEGER       NOT NULL DEFAULT 0,
            wait_minutes_actual       INTEGER,
            eta_scheduled             TIMESTAMPTZ,
            eta_current               TIMESTAMPTZ,
            arrived_at                TIMESTAMPTZ,
            completed_at              TIMESTAMPTZ,
            distance_from_prev_km     NUMERIC(8,2),
            pod_required              podtype       NOT NULL DEFAULT 'signature',
            skip_reason               TEXT,
            passengers_count          INTEGER       NOT NULL DEFAULT 0,
            passengers_checked_in     INTEGER       NOT NULL DEFAULT 0,
            driver_notes              TEXT,
            created_at                TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at                TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            UNIQUE(route_header_id, sequence_order)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_rd_header_id      ON route_details(route_header_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_rd_dest_warehouse  ON route_details(destination_warehouse_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_rd_status          ON route_details(status)"))

    # ── SHIPMENT_ITEMS ────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shipment_items (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            route_detail_id     UUID NOT NULL REFERENCES route_details(id) ON DELETE CASCADE,
            line_number         INTEGER       NOT NULL,
            package_code        VARCHAR(100),
            description         VARCHAR(255),
            item_type           VARCHAR(30)   NOT NULL DEFAULT 'box',
            quantity            INTEGER       NOT NULL DEFAULT 1,
            weight_lbs          NUMERIC(8,2)  NOT NULL DEFAULT 0,
            length_in           NUMERIC(6,2),
            width_in            NUMERIC(6,2),
            height_in           NUMERIC(6,2),
            volume_ft3          NUMERIC(8,2),
            declared_value      NUMERIC(10,2) NOT NULL DEFAULT 0,
            is_fragile          BOOLEAN       NOT NULL DEFAULT FALSE,
            is_hazmat           BOOLEAN       NOT NULL DEFAULT FALSE,
            hazmat_class        VARCHAR(20),
            requires_signature  BOOLEAN       NOT NULL DEFAULT TRUE,
            picked_up           BOOLEAN       NOT NULL DEFAULT FALSE,
            picked_up_at        TIMESTAMPTZ,
            delivered           BOOLEAN       NOT NULL DEFAULT FALSE,
            delivered_at        TIMESTAMPTZ,
            notes               TEXT,
            created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            UNIQUE(route_detail_id, line_number)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_si_detail_id    ON shipment_items(route_detail_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_si_package_code  ON shipment_items(package_code)"))

    # ── TRIGGERS — totales automáticos ────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE OR REPLACE FUNCTION fn_recalc_route_header_totals()
        RETURNS TRIGGER AS $$
        DECLARE v_header_id UUID;
        BEGIN
            v_header_id := COALESCE(NEW.route_header_id, OLD.route_header_id);
            UPDATE route_headers rh SET
                total_stops          = (SELECT COUNT(*) FROM route_details WHERE route_header_id = v_header_id),
                completed_stops      = (SELECT COUNT(*) FROM route_details WHERE route_header_id = v_header_id AND status = 'completed'),
                total_packages       = (SELECT COALESCE(SUM(packages_count),0) FROM route_details WHERE route_header_id = v_header_id),
                total_weight_lbs     = (SELECT COALESCE(SUM(weight_lbs),0)     FROM route_details WHERE route_header_id = v_header_id),
                total_volume_ft3     = (SELECT COALESCE(SUM(volume_ft3),0)     FROM route_details WHERE route_header_id = v_header_id),
                total_cargo_value    = (SELECT COALESCE(SUM(cargo_value),0)    FROM route_details WHERE route_header_id = v_header_id),
                has_fragile_items    = (SELECT COALESCE(BOOL_OR(is_fragile),FALSE)           FROM route_details WHERE route_header_id = v_header_id),
                has_hazmat_items     = (SELECT COALESCE(BOOL_OR(is_hazmat),FALSE)             FROM route_details WHERE route_header_id = v_header_id),
                requires_temp_control= (SELECT COALESCE(BOOL_OR(is_temp_controlled),FALSE)   FROM route_details WHERE route_header_id = v_header_id),
                requires_insurance   = (SELECT COALESCE(BOOL_OR(requires_insurance),FALSE)   FROM route_details WHERE route_header_id = v_header_id),
                updated_at           = NOW()
            WHERE rh.id = v_header_id;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """))
    conn.execute(sa.text("DROP TRIGGER IF EXISTS trg_route_details_totals ON route_details"))
    conn.execute(sa.text("""
        CREATE TRIGGER trg_route_details_totals
        AFTER INSERT OR UPDATE OR DELETE ON route_details
        FOR EACH ROW EXECUTE FUNCTION fn_recalc_route_header_totals()
    """))

    conn.execute(sa.text("""
        CREATE OR REPLACE FUNCTION fn_recalc_route_detail_totals()
        RETURNS TRIGGER AS $$
        DECLARE v_detail_id UUID;
        BEGIN
            v_detail_id := COALESCE(NEW.route_detail_id, OLD.route_detail_id);
            UPDATE route_details rd SET
                packages_count = (SELECT COALESCE(SUM(quantity),0)       FROM shipment_items WHERE route_detail_id = v_detail_id),
                weight_lbs     = (SELECT COALESCE(SUM(weight_lbs),0)     FROM shipment_items WHERE route_detail_id = v_detail_id),
                volume_ft3     = (SELECT COALESCE(SUM(volume_ft3),0)     FROM shipment_items WHERE route_detail_id = v_detail_id),
                cargo_value    = (SELECT COALESCE(SUM(declared_value),0) FROM shipment_items WHERE route_detail_id = v_detail_id),
                is_fragile     = (SELECT COALESCE(BOOL_OR(is_fragile),FALSE) FROM shipment_items WHERE route_detail_id = v_detail_id),
                is_hazmat      = (SELECT COALESCE(BOOL_OR(is_hazmat),FALSE)  FROM shipment_items WHERE route_detail_id = v_detail_id),
                updated_at     = NOW()
            WHERE rd.id = v_detail_id;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """))
    conn.execute(sa.text("DROP TRIGGER IF EXISTS trg_shipment_items_totals ON shipment_items"))
    conn.execute(sa.text("""
        CREATE TRIGGER trg_shipment_items_totals
        AFTER INSERT OR UPDATE OR DELETE ON shipment_items
        FOR EACH ROW EXECUTE FUNCTION fn_recalc_route_detail_totals()
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TRIGGER IF EXISTS trg_shipment_items_totals ON shipment_items"))
    conn.execute(sa.text("DROP TRIGGER IF EXISTS trg_route_details_totals ON route_details"))
    conn.execute(sa.text("DROP FUNCTION IF EXISTS fn_recalc_route_detail_totals"))
    conn.execute(sa.text("DROP FUNCTION IF EXISTS fn_recalc_route_header_totals"))
    conn.execute(sa.text("DROP TABLE IF EXISTS shipment_items CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS route_details CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS route_headers CASCADE"))
    conn.execute(sa.text("ALTER TABLE vehicles DROP COLUMN IF EXISTS transport_company_id"))
    conn.execute(sa.text("DROP TABLE IF EXISTS transport_companies CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS warehouse_users CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS warehouses CASCADE"))
    conn.execute(sa.text("ALTER TABLE companies DROP COLUMN IF EXISTS billing_mode"))
    conn.execute(sa.text("ALTER TABLE companies DROP COLUMN IF EXISTS is_primary_company"))
    conn.execute(sa.text("ALTER TABLE companies DROP COLUMN IF EXISTS holding_id"))
    conn.execute(sa.text("DROP TABLE IF EXISTS holding_users CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS holdings CASCADE"))

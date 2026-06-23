"""initial — Muevo B2B schema

Revision ID: 001_initial
Revises:
Create Date: 2026-05-31

Nota: usa SQL puro para evitar conflictos con los ENUMs de SQLAlchemy.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── ENUMS — solo si no existen ────────────────────────────────────────────
    # Crear ENUMs solo si no existen (compatible con todas las versiones de PostgreSQL)
    conn.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'userrole') THEN
                CREATE TYPE userrole AS ENUM ('driver','corp_admin','muevo_admin','dispatcher');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'vehicletype') THEN
                CREATE TYPE vehicletype AS ENUM ('moto','sedan','furgoneta','furgon');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'servicemode') THEN
                CREATE TYPE servicemode AS ENUM ('mensajeria','logistica','empleados','mixto');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'routestatus') THEN
                CREATE TYPE routestatus AS ENUM ('draft','published','assigned','in_progress','completed','cancelled','failed');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'stopstatus') THEN
                CREATE TYPE stopstatus AS ENUM ('pending','in_transit','arrived','completed','skipped','failed');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'podtype') THEN
                CREATE TYPE podtype AS ENUM ('signature','photo','both','qr_scan','pin_code');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'certstatus') THEN
                CREATE TYPE certstatus AS ENUM ('pending','active','expired','rejected');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'paymentstatus') THEN
                CREATE TYPE paymentstatus AS ENUM ('pending','processing','paid','failed','refunded');
            END IF;
        END $$;
    """))

    # ── USERS ─────────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS users (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email               VARCHAR(255) NOT NULL UNIQUE,
            phone               VARCHAR(30)  UNIQUE,
            full_name           VARCHAR(255) NOT NULL,
            avatar_initials     VARCHAR(4),
            avatar_url          VARCHAR(512),
            role                userrole     NOT NULL DEFAULT 'driver',
            hashed_password     VARCHAR(255) NOT NULL,
            is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
            is_verified         BOOLEAN      NOT NULL DEFAULT FALSE,
            preferred_language  VARCHAR(10)  NOT NULL DEFAULT 'es',
            timezone            VARCHAR(64)  NOT NULL DEFAULT 'America/New_York',
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            last_login_at       TIMESTAMPTZ
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_users_email ON users(email)"))

    # ── DRIVERS ───────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS drivers (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id             UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            is_online           BOOLEAN      NOT NULL DEFAULT FALSE,
            is_approved         BOOLEAN      NOT NULL DEFAULT FALSE,
            current_lat         FLOAT,
            current_lng         FLOAT,
            last_location_at    TIMESTAMPTZ,
            service_modes       TEXT[]       NOT NULL DEFAULT '{}',
            total_routes        INTEGER      NOT NULL DEFAULT 0,
            completed_routes    INTEGER      NOT NULL DEFAULT 0,
            cancelled_routes    INTEGER      NOT NULL DEFAULT 0,
            avg_rating          NUMERIC(3,2) NOT NULL DEFAULT 5.00,
            total_earnings_net  NUMERIC(12,2) NOT NULL DEFAULT 0.00,
            total_km_driven     NUMERIC(10,2) NOT NULL DEFAULT 0.00,
            stripe_account_id   VARCHAR(255),
            stripe_onboarded    BOOLEAN      NOT NULL DEFAULT FALSE,
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_drivers_is_online ON drivers(is_online)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_drivers_location  ON drivers(current_lat, current_lng)"))

    # ── VEHICLES ──────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS vehicles (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            driver_id            UUID NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
            is_active            BOOLEAN      NOT NULL DEFAULT TRUE,
            vehicle_type         vehicletype  NOT NULL,
            make                 VARCHAR(100) NOT NULL,
            model                VARCHAR(100) NOT NULL,
            year                 INTEGER      NOT NULL CHECK (year >= 2000 AND year <= 2030),
            color                VARCHAR(50)  NOT NULL,
            plate                VARCHAR(20)  NOT NULL UNIQUE,
            vin                  VARCHAR(17),
            payload_kg           NUMERIC(8,2),
            volume_m3            NUMERIC(6,2),
            passenger_seats      INTEGER      NOT NULL DEFAULT 1,
            mpg                  NUMERIC(6,2),
            fuel_type            VARCHAR(20)  NOT NULL DEFAULT 'gasoline',
            odometer_miles       INTEGER      NOT NULL DEFAULT 0,
            odometer_updated_at  TIMESTAMPTZ,
            insurance_url        VARCHAR(512),
            insurance_expires    TIMESTAMPTZ,
            inspection_url       VARCHAR(512),
            inspection_expires   TIMESTAMPTZ,
            registration_url     VARCHAR(512),
            created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_vehicles_driver_id ON vehicles(driver_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_vehicles_plate     ON vehicles(plate)"))

    # ── CERTIFICATIONS ────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS certifications (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            driver_id          UUID NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
            cert_type          VARCHAR(100) NOT NULL,
            label              VARCHAR(255) NOT NULL,
            status             certstatus   NOT NULL DEFAULT 'pending',
            document_url       VARCHAR(512),
            issued_at          TIMESTAMPTZ,
            expires_at         TIMESTAMPTZ,
            issuing_authority  VARCHAR(255),
            notes              TEXT,
            reviewed_by        UUID REFERENCES users(id),
            reviewed_at        TIMESTAMPTZ,
            rejection_reason   TEXT,
            created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_certs_driver_id ON certifications(driver_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_certs_expires   ON certifications(expires_at)"))

    # ── COMPANIES ─────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS companies (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name                 VARCHAR(255) NOT NULL,
            legal_name           VARCHAR(255),
            tax_id               VARCHAR(50)  UNIQUE,
            industry             VARCHAR(100),
            contact_name         VARCHAR(255),
            contact_email        VARCHAR(255),
            contact_phone        VARCHAR(30),
            address_line1        VARCHAR(255),
            address_line2        VARCHAR(255),
            city                 VARCHAR(100),
            state                VARCHAR(50),
            zip_code             VARCHAR(20),
            country              VARCHAR(50)  NOT NULL DEFAULT 'US',
            allowed_modes        TEXT[]       NOT NULL DEFAULT '{}',
            required_certs       TEXT[]       NOT NULL DEFAULT '{}',
            billing_email        VARCHAR(255),
            payment_terms_days   INTEGER      NOT NULL DEFAULT 30,
            stripe_customer_id   VARCHAR(255),
            credit_limit         NUMERIC(12,2) NOT NULL DEFAULT 0.00,
            is_active            BOOLEAN      NOT NULL DEFAULT TRUE,
            logo_url             VARCHAR(512),
            notes                TEXT,
            created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """))

    # ── COMPANY ADMINS ────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS company_admins (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id   UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            user_id      UUID NOT NULL REFERENCES users(id)     ON DELETE CASCADE,
            is_primary   BOOLEAN NOT NULL DEFAULT FALSE,
            can_dispatch BOOLEAN NOT NULL DEFAULT TRUE,
            can_invoice  BOOLEAN NOT NULL DEFAULT FALSE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(company_id, user_id)
        )
    """))

    # ── CONTRACTS ─────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS contracts (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id            UUID         NOT NULL REFERENCES companies(id),
            contract_number       VARCHAR(50)  NOT NULL UNIQUE,
            service_mode          servicemode  NOT NULL,
            vehicle_type          vehicletype,
            base_rate             NUMERIC(10,2) NOT NULL,
            rate_per_km           NUMERIC(8,4),
            rate_per_stop         NUMERIC(8,2),
            muevo_commission_pct  NUMERIC(5,2)  NOT NULL DEFAULT 12.00,
            max_delay_minutes     INTEGER       NOT NULL DEFAULT 15,
            penalty_per_delay     NUMERIC(8,2)  NOT NULL DEFAULT 0.00,
            bonus_on_time         NUMERIC(8,2)  NOT NULL DEFAULT 0.00,
            start_date            TIMESTAMPTZ   NOT NULL,
            end_date              TIMESTAMPTZ,
            is_active             BOOLEAN       NOT NULL DEFAULT TRUE,
            signed_document_url   VARCHAR(512),
            notes                 TEXT,
            created_at            TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at            TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
    """))

    # ── ROUTES ────────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS routes (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id             UUID          NOT NULL REFERENCES companies(id),
            contract_id            UUID          REFERENCES contracts(id),
            driver_id              UUID          REFERENCES drivers(id),
            vehicle_id             UUID          REFERENCES vehicles(id),
            dispatched_by          UUID          REFERENCES users(id),
            route_number           VARCHAR(50)   NOT NULL UNIQUE,
            title                  VARCHAR(255)  NOT NULL,
            service_mode           servicemode   NOT NULL,
            status                 routestatus   NOT NULL DEFAULT 'draft',
            scheduled_date         TIMESTAMPTZ   NOT NULL,
            scheduled_start        TIMESTAMPTZ   NOT NULL,
            scheduled_end          TIMESTAMPTZ   NOT NULL,
            actual_start           TIMESTAMPTZ,
            actual_end             TIMESTAMPTZ,
            total_stops            INTEGER       NOT NULL DEFAULT 0,
            completed_stops        INTEGER       NOT NULL DEFAULT 0,
            total_km_estimated     NUMERIC(8,2),
            total_km_actual        NUMERIC(8,2),
            origin_address         VARCHAR(255),
            required_vehicle_type  vehicletype,
            required_certs         TEXT[]        NOT NULL DEFAULT '{}',
            gross_pay              NUMERIC(10,2) NOT NULL,
            muevo_commission       NUMERIC(10,2) NOT NULL,
            fuel_cost_estimated    NUMERIC(8,2),
            wear_cost_estimated    NUMERIC(8,2),
            net_pay_estimated      NUMERIC(10,2),
            net_pay_actual         NUMERIC(10,2),
            margin_pct             NUMERIC(5,2),
            sla_penalty            NUMERIC(8,2)  NOT NULL DEFAULT 0.00,
            sla_bonus              NUMERIC(8,2)  NOT NULL DEFAULT 0.00,
            on_time_pct            NUMERIC(5,2),
            driver_notes           TEXT,
            internal_notes         TEXT,
            optimized_order        JSONB,
            loading_instructions   JSONB,
            passenger_manifest     JSONB,
            created_at             TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at             TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_routes_company_id     ON routes(company_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_routes_driver_id      ON routes(driver_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_routes_status         ON routes(status)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_routes_scheduled_date ON routes(scheduled_date)"))

    # ── STOPS ─────────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS stops (
            id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            route_id                  UUID          NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
            sequence_order            INTEGER       NOT NULL,
            original_order            INTEGER,
            status                    stopstatus    NOT NULL DEFAULT 'pending',
            address_line1             VARCHAR(255)  NOT NULL,
            address_line2             VARCHAR(255),
            city                      VARCHAR(100)  NOT NULL,
            state                     VARCHAR(50)   NOT NULL,
            zip_code                  VARCHAR(20),
            lat                       FLOAT,
            lng                       FLOAT,
            company_name              VARCHAR(255),
            contact_name              VARCHAR(255),
            contact_phone             VARCHAR(30),
            contact_email             VARCHAR(255),
            floor_suite               VARCHAR(100),
            access_notes              TEXT,
            package_code              VARCHAR(100),
            package_description       VARCHAR(255),
            package_weight_kg         NUMERIC(8,2),
            is_fragile                BOOLEAN       NOT NULL DEFAULT FALSE,
            is_urgent                 BOOLEAN       NOT NULL DEFAULT FALSE,
            eta_scheduled             TIMESTAMPTZ,
            eta_current               TIMESTAMPTZ,
            arrived_at                TIMESTAMPTZ,
            completed_at              TIMESTAMPTZ,
            wait_minutes_estimated    INTEGER       NOT NULL DEFAULT 0,
            wait_minutes_actual       INTEGER,
            distance_from_prev_km     NUMERIC(8,2),
            pod_required              podtype       NOT NULL DEFAULT 'signature',
            skip_reason               TEXT,
            passengers_count          INTEGER       NOT NULL DEFAULT 0,
            passengers_checked_in     INTEGER       NOT NULL DEFAULT 0,
            loading_notes             TEXT,
            dock_number               VARCHAR(50),
            driver_notes              TEXT,
            created_at                TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at                TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            UNIQUE(route_id, sequence_order)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_stops_route_id     ON stops(route_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_stops_status       ON stops(status)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_stops_package_code ON stops(package_code)"))

    # ── DELIVERIES ────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS deliveries (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            stop_id               UUID          NOT NULL UNIQUE REFERENCES stops(id) ON DELETE CASCADE,
            driver_id             UUID          NOT NULL REFERENCES drivers(id),
            pod_type              podtype       NOT NULL,
            signature_url         VARCHAR(512),
            signer_name           VARCHAR(255),
            signer_id_number      VARCHAR(100),
            photo_url             VARCHAR(512),
            photo_url_2           VARCHAR(512),
            scanned_code          VARCHAR(255),
            scan_verified         BOOLEAN       NOT NULL DEFAULT FALSE,
            pin_code              VARCHAR(10),
            pin_verified          BOOLEAN       NOT NULL DEFAULT FALSE,
            delivery_lat          FLOAT,
            delivery_lng          FLOAT,
            location_accuracy_m   FLOAT,
            delivered_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            on_time               BOOLEAN,
            delay_minutes         INTEGER       NOT NULL DEFAULT 0,
            driver_notes          TEXT,
            recipient_notes       TEXT,
            validated_by_client   BOOLEAN,
            validated_at          TIMESTAMPTZ,
            dispute_reason        TEXT,
            created_at            TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_deliveries_stop_id      ON deliveries(stop_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_deliveries_driver_id    ON deliveries(driver_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_deliveries_delivered_at ON deliveries(delivered_at)"))

    # ── EARNINGS ──────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS earnings (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            route_id              UUID          NOT NULL UNIQUE REFERENCES routes(id),
            driver_id             UUID          NOT NULL REFERENCES drivers(id),
            company_id            UUID          NOT NULL REFERENCES companies(id),
            gross_pay             NUMERIC(10,2) NOT NULL,
            muevo_commission_pct  NUMERIC(5,2)  NOT NULL,
            muevo_commission_amt  NUMERIC(10,2) NOT NULL,
            fuel_cost             NUMERIC(8,2)  NOT NULL,
            wear_cost             NUMERIC(8,2)  NOT NULL,
            total_costs           NUMERIC(10,2) NOT NULL,
            sla_bonus             NUMERIC(8,2)  NOT NULL DEFAULT 0.00,
            sla_penalty           NUMERIC(8,2)  NOT NULL DEFAULT 0.00,
            tip_amount            NUMERIC(8,2)  NOT NULL DEFAULT 0.00,
            net_pay               NUMERIC(10,2) NOT NULL,
            margin_pct            NUMERIC(5,2)  NOT NULL,
            total_km              NUMERIC(8,2),
            total_stops           INTEGER,
            completed_stops       INTEGER,
            gas_price_used        NUMERIC(6,3),
            payment_status        paymentstatus NOT NULL DEFAULT 'pending',
            stripe_transfer_id    VARCHAR(255),
            paid_at               TIMESTAMPTZ,
            period_start          TIMESTAMPTZ,
            period_end            TIMESTAMPTZ,
            created_at            TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at            TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_earnings_driver_id      ON earnings(driver_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_earnings_company_id     ON earnings(company_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_earnings_payment_status ON earnings(payment_status)"))

    # ── DRIVER METRICS ────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS driver_metrics (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            driver_id            UUID          NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
            metric_date          TIMESTAMPTZ   NOT NULL,
            routes_completed     INTEGER       NOT NULL DEFAULT 0,
            stops_completed      INTEGER       NOT NULL DEFAULT 0,
            km_driven            NUMERIC(8,2)  NOT NULL DEFAULT 0.00,
            active_minutes       INTEGER       NOT NULL DEFAULT 0,
            idle_minutes         INTEGER       NOT NULL DEFAULT 0,
            gross_earned         NUMERIC(10,2) NOT NULL DEFAULT 0.00,
            net_earned           NUMERIC(10,2) NOT NULL DEFAULT 0.00,
            fuel_spent           NUMERIC(8,2)  NOT NULL DEFAULT 0.00,
            bonuses_earned       NUMERIC(8,2)  NOT NULL DEFAULT 0.00,
            penalties_incurred   NUMERIC(8,2)  NOT NULL DEFAULT 0.00,
            avg_net_per_hour     NUMERIC(8,2),
            avg_net_per_km       NUMERIC(8,4),
            on_time_rate         NUMERIC(5,2),
            completion_rate      NUMERIC(5,2),
            created_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            UNIQUE(driver_id, metric_date)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_driver_metrics_driver_id ON driver_metrics(driver_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_driver_metrics_date      ON driver_metrics(metric_date)"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS driver_metrics  CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS earnings        CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS deliveries      CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS stops           CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS routes          CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS contracts       CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS company_admins  CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS companies       CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS certifications  CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS vehicles        CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS drivers         CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS users           CASCADE"))
    conn.execute(sa.text("DROP TYPE IF EXISTS paymentstatus"))
    conn.execute(sa.text("DROP TYPE IF EXISTS certstatus"))
    conn.execute(sa.text("DROP TYPE IF EXISTS podtype"))
    conn.execute(sa.text("DROP TYPE IF EXISTS stopstatus"))
    conn.execute(sa.text("DROP TYPE IF EXISTS routestatus"))
    conn.execute(sa.text("DROP TYPE IF EXISTS servicemode"))
    conn.execute(sa.text("DROP TYPE IF EXISTS vehicletype"))
    conn.execute(sa.text("DROP TYPE IF EXISTS userrole"))
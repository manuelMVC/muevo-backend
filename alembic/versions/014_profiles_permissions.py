"""add modules, actions, permissions, profiles, profile_permissions, holding_user_profiles

Revision ID: 014_profiles_permissions
Revises: 013_vehicle_types
Create Date: 2026-07-01

Implementa el modelo de perfiles y permisos granulares:
  - modules        — catálogo de módulos del sistema
  - actions        — catálogo de acciones posibles
  - permissions    — combinación módulo + acción
  - profiles       — agrupación de permisos reutilizables
  - profile_permissions — relación perfil ↔ permiso
  - holding_user_profiles — asignación de perfil a usuario con alcance company/warehouse

Siembra:
  - 8 módulos del sistema
  - 7 acciones del sistema
  - 40 permisos (todas las combinaciones válidas)
  - 6 perfiles del sistema con sus permisos
"""

from alembic import op
import sqlalchemy as sa

revision      = '014_profiles_permissions'
down_revision = '013_vehicle_types'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. modules ────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS modules (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code        VARCHAR(60)  NOT NULL UNIQUE,
            name        VARCHAR(120) NOT NULL,
            description TEXT,
            sort_order  INTEGER      NOT NULL DEFAULT 0
        )
    """))

    # ── 2. actions ────────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS actions (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code        VARCHAR(60)  NOT NULL UNIQUE,
            name        VARCHAR(120) NOT NULL,
            description TEXT,
            sort_order  INTEGER      NOT NULL DEFAULT 0
        )
    """))

    # ── 3. permissions ────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS permissions (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code        VARCHAR(120) NOT NULL UNIQUE,
            name        VARCHAR(200) NOT NULL,
            description TEXT,
            module_id   UUID NOT NULL REFERENCES modules(id),
            action_id   UUID NOT NULL REFERENCES actions(id),
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(module_id, action_id)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_permissions_module_id ON permissions(module_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_permissions_action_id ON permissions(action_id)"))

    # ── 4. profiles ───────────────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profiles (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code        VARCHAR(60)  NOT NULL UNIQUE,
            name        VARCHAR(120) NOT NULL,
            description TEXT,
            is_system   BOOLEAN NOT NULL DEFAULT FALSE,
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    # ── 5. profile_permissions ────────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_permissions (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            profile_id    UUID NOT NULL REFERENCES profiles(id)    ON DELETE CASCADE,
            permission_id UUID NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(profile_id, permission_id)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pp_profile_id    ON profile_permissions(profile_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pp_permission_id ON profile_permissions(permission_id)"))

    # ── 6. holding_user_profiles ──────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS holding_user_profiles (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            holding_user_id UUID NOT NULL REFERENCES holding_users(id) ON DELETE CASCADE,
            profile_id      UUID NOT NULL REFERENCES profiles(id)      ON DELETE CASCADE,
            company_id      UUID REFERENCES companies(id)  ON DELETE CASCADE,
            warehouse_id    UUID REFERENCES warehouses(id) ON DELETE CASCADE,
            granted_by_id   UUID REFERENCES users(id),
            granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_hup_holding_user_id ON holding_user_profiles(holding_user_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_hup_profile_id      ON holding_user_profiles(profile_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_hup_company_id      ON holding_user_profiles(company_id)"))

    # ── 7. Seed: modules ──────────────────────────────────────────────────────
    conn.execute(sa.text("""
        INSERT INTO modules (code, name, description, sort_order) VALUES
        ('companies',  'Compañías',        'Gestión de compañías del holding',           1),
        ('warehouses', 'Warehouses',        'Gestión de warehouses por compañía',         2),
        ('users',      'Usuarios',          'Gestión de usuarios y perfiles',              3),
        ('routes',     'Rutas',             'Gestión de rutas de entrega',                 4),
        ('batches',    'Lotes',             'Gestión de lotes de rutas',                   5),
        ('billing',    'Facturación',       'Acceso a facturación y pagos',               6),
        ('reports',    'Reportes',          'Acceso a reportes y analytics',              7),
        ('config',     'Configuración',     'Gestión de catálogos y configuración',       8)
        ON CONFLICT (code) DO NOTHING
    """))

    # ── 8. Seed: actions ──────────────────────────────────────────────────────
    conn.execute(sa.text("""
        INSERT INTO actions (code, name, description, sort_order) VALUES
        ('view',    'Ver',       'Visualizar registros del módulo',                  1),
        ('create',  'Crear',     'Crear nuevos registros',                           2),
        ('edit',    'Editar',    'Modificar registros existentes',                   3),
        ('delete',  'Eliminar',  'Eliminar o desactivar registros',                  4),
        ('approve', 'Aprobar',   'Aprobar o confirmar registros',                    5),
        ('cancel',  'Cancelar',  'Cancelar operaciones o registros',                 6),
        ('export',  'Exportar',  'Exportar datos en CSV, PDF u otros formatos',      7)
        ON CONFLICT (code) DO NOTHING
    """))

    # ── 9. Seed: permissions (module.action combos) ───────────────────────────
    conn.execute(sa.text("""
        INSERT INTO permissions (code, name, module_id, action_id)
        SELECT
            m.code || '.' || a.code,
            m.name || ' — ' || a.name,
            m.id,
            a.id
        FROM modules m
        CROSS JOIN actions a
        WHERE (m.code, a.code) IN (
            ('companies','view'),('companies','create'),('companies','edit'),('companies','delete'),
            ('warehouses','view'),('warehouses','create'),('warehouses','edit'),('warehouses','delete'),
            ('users','view'),('users','create'),('users','edit'),('users','delete'),
            ('routes','view'),('routes','create'),('routes','edit'),('routes','approve'),('routes','cancel'),('routes','export'),
            ('batches','view'),('batches','create'),('batches','edit'),('batches','approve'),('batches','cancel'),('batches','export'),
            ('billing','view'),('billing','export'),
            ('reports','view'),('reports','export'),
            ('config','view'),('config','create'),('config','edit'),('config','delete')
        )
        ON CONFLICT (code) DO NOTHING
    """))

    # ── 10. Seed: profiles ────────────────────────────────────────────────────
    conn.execute(sa.text("""
        INSERT INTO profiles (code, name, description, is_system) VALUES
        ('super_admin',      'Super Admin',        'Acceso total al holding — gestiona compañías, warehouses, usuarios y configuración', TRUE),
        ('company_admin',    'Admin de Compañía',  'Administra una compañía: usuarios, warehouses, rutas y lotes',                      TRUE),
        ('warehouse_admin',  'Admin de Warehouse', 'Administra un warehouse específico: rutas y lotes',                                 TRUE),
        ('operator',         'Operador',           'Crea y gestiona rutas y lotes, sin acceso a configuración',                         TRUE),
        ('viewer',           'Solo Lectura',       'Visualiza información sin poder crear ni modificar',                                TRUE),
        ('billing_manager',  'Gestor de Facturación', 'Acceso a facturación, reportes financieros y exportación',                      TRUE)
        ON CONFLICT (code) DO NOTHING
    """))

    # ── 11. Assign permissions to profiles ────────────────────────────────────
    # super_admin — all permissions
    conn.execute(sa.text("""
        INSERT INTO profile_permissions (profile_id, permission_id)
        SELECT p.id, pm.id FROM profiles p, permissions pm
        WHERE p.code = 'super_admin'
        ON CONFLICT DO NOTHING
    """))

    # company_admin — all except companies.delete, config.*
    conn.execute(sa.text("""
        INSERT INTO profile_permissions (profile_id, permission_id)
        SELECT p.id, pm.id
        FROM profiles p, permissions pm
        JOIN modules m  ON pm.module_id = m.id
        JOIN actions a  ON pm.action_id = a.id
        WHERE p.code = 'company_admin'
          AND NOT (m.code = 'companies' AND a.code = 'delete')
          AND NOT (m.code = 'config')
        ON CONFLICT DO NOTHING
    """))

    # warehouse_admin — routes/batches all, warehouses view/edit, users view/create/edit, billing view, reports view
    conn.execute(sa.text("""
        INSERT INTO profile_permissions (profile_id, permission_id)
        SELECT p.id, pm.id
        FROM profiles p, permissions pm
        JOIN modules m ON pm.module_id = m.id
        JOIN actions a ON pm.action_id = a.id
        WHERE p.code = 'warehouse_admin'
          AND (
            m.code IN ('routes', 'batches')
            OR (m.code = 'warehouses' AND a.code IN ('view','edit'))
            OR (m.code = 'users'      AND a.code IN ('view','create','edit'))
            OR (m.code = 'billing'    AND a.code = 'view')
            OR (m.code = 'reports'    AND a.code = 'view')
          )
        ON CONFLICT DO NOTHING
    """))

    # operator — routes/batches create/view/edit/approve/cancel, billing view
    conn.execute(sa.text("""
        INSERT INTO profile_permissions (profile_id, permission_id)
        SELECT p.id, pm.id
        FROM profiles p, permissions pm
        JOIN modules m ON pm.module_id = m.id
        JOIN actions a ON pm.action_id = a.id
        WHERE p.code = 'operator'
          AND (
            (m.code IN ('routes','batches') AND a.code IN ('view','create','edit','approve','cancel'))
            OR (m.code = 'billing' AND a.code = 'view')
          )
        ON CONFLICT DO NOTHING
    """))

    # viewer — view only on everything
    conn.execute(sa.text("""
        INSERT INTO profile_permissions (profile_id, permission_id)
        SELECT p.id, pm.id
        FROM profiles p, permissions pm
        JOIN actions a ON pm.action_id = a.id
        WHERE p.code = 'viewer'
          AND a.code = 'view'
        ON CONFLICT DO NOTHING
    """))

    # billing_manager — billing/reports all, routes/batches view/export
    conn.execute(sa.text("""
        INSERT INTO profile_permissions (profile_id, permission_id)
        SELECT p.id, pm.id
        FROM profiles p, permissions pm
        JOIN modules m ON pm.module_id = m.id
        JOIN actions a ON pm.action_id = a.id
        WHERE p.code = 'billing_manager'
          AND (
            m.code IN ('billing','reports')
            OR (m.code IN ('routes','batches') AND a.code IN ('view','export'))
          )
        ON CONFLICT DO NOTHING
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS holding_user_profiles  CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS profile_permissions    CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS profiles               CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS permissions            CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS actions                CASCADE"))
    conn.execute(sa.text("DROP TABLE IF EXISTS modules                CASCADE"))

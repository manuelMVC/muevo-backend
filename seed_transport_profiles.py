"""
seed_transport_profiles.py — Perfiles RBAC para usuarios de empresas de
transporte, y usuarios de prueba para cada uno.

Contexto: los perfiles existentes (super_admin, company_admin, ...) cuelgan de
HoldingUser, así que un admin de transporte no podía tener ninguno. Este script,
de forma idempotente:
  1. Agrega el módulo 'fleet' (Flota) con view/create/edit/delete.
  2. Crea 4 perfiles de transporte con sus permisos:
       transport_admin, transport_dispatcher, transport_billing, transport_viewer
  3. Asigna perfil (holding_user_profiles) a los admins de transporte existentes.
     Para poder colgar el perfil, cada usuario de transporte también recibe un
     HoldingUser (sin acceso a ninguna company del warehouse). El login lo manda
     igual al portal de transporte porque resolve_user_portal() revisa
     transport_company_admins antes que holding_users.
  4. Crea 3 usuarios de prueba en Rapid Courier Orlando LLC, uno por cada perfil
     que todavía no tenía usuario.

Uso:
    cd C:\\Claude\\muevo-backend
    .venv\\Scripts\\python seed_transport_profiles.py
"""

import sys, os, uuid
sys.path.insert(0, os.path.dirname(__file__))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:Semeolvido.01@localhost:5432/muevodb")

from models import (
    Module, Action, Permission, Profile, ProfilePermission, HoldingUser, HoldingUserProfile, Holding,
    User, UserRole, TransportCompany, TransportCompanyAdmin,
)
from main import hash_password

engine = create_engine(DATABASE_URL, echo=False)

TEST_PASSWORD = "muevo123"

NEW_MODULE = ("fleet", "Flota", ["view", "create", "edit", "delete"])

# code, nombre, descripción, permisos
PROFILES = [
    ("transport_admin", "Admin de Transporte",
     "Opera la empresa de transporte completa: rutas, ofertas, flota, facturación e incidencias.",
     ["routes.view", "routes.edit", "routes.approve", "routes.cancel", "routes.export",
      "pricing.view", "pricing.create", "pricing.edit", "pricing.cancel",
      "fleet.view", "fleet.create", "fleet.edit", "fleet.delete",
      "billing.view", "billing.export",
      "incidents.view", "incidents.create", "incidents.edit"]),
    ("transport_dispatcher", "Despachador de Transporte",
     "Acepta o rechaza rutas, oferta en el marketplace y asigna vehículos. Sin acceso a facturación.",
     ["routes.view", "routes.edit", "routes.approve", "routes.cancel",
      "pricing.view", "pricing.create", "pricing.edit",
      "fleet.view", "fleet.edit",
      "incidents.view", "incidents.create", "incidents.edit"]),
    ("transport_billing", "Facturación de Transporte",
     "Consulta y exporta los pagos de rutas completadas. Solo lectura del resto.",
     ["billing.view", "billing.export", "routes.view", "pricing.view"]),
    ("transport_viewer", "Solo Lectura de Transporte",
     "Puede ver rutas, ofertas, flota, facturación e incidencias, sin modificar nada.",
     ["routes.view", "pricing.view", "fleet.view", "billing.view", "incidents.view"]),
]

# Admins de transporte que ya existen -> perfil
EXISTING_ASSIGNMENTS = [
    ("transport@muevo.app", "transport_admin"),
    ("sunshine@muevo.app",  "transport_admin"),
]

# Usuarios de prueba nuevos (Rapid Courier Orlando LLC)
TEST_USERS = [
    ("transport.dispatcher@muevo.app", "Test Despachador Transporte", "transport_dispatcher"),
    ("transport.billing@muevo.app",    "Test Facturación Transporte", "transport_billing"),
    ("transport.viewer@muevo.app",     "Test Solo Lectura Transporte", "transport_viewer"),
]
TEST_COMPANY_NAME = "Rapid Courier Orlando LLC"


def main():
    with Session(engine) as db:
        # ── Módulo + permisos 'fleet' ────────────────────────────────────────
        code, name, actions = NEW_MODULE
        module = db.execute(select(Module).where(Module.code == code)).scalar_one_or_none()
        if not module:
            module = Module(id=uuid.uuid4(), code=code, name=name, sort_order=120)
            db.add(module)
            db.flush()
            print(f"+ Module creado: {code}")
        action_by_code = {a.code: a for a in db.execute(select(Action)).scalars().all()}
        permission_by_code = {p.code: p for p in db.execute(select(Permission)).scalars().all()}
        for action_code in actions:
            perm_code = f"{code}.{action_code}"
            if perm_code in permission_by_code:
                continue
            action = action_by_code[action_code]
            perm = Permission(id=uuid.uuid4(), code=perm_code, name=f"{name} — {action.name}",
                              module_id=module.id, action_id=action.id, is_active=True)
            db.add(perm)
            db.flush()
            permission_by_code[perm_code] = perm
            print(f"+ Permission creado: {perm_code}")
        db.commit()

        # ── Perfiles ─────────────────────────────────────────────────────────
        profile_by_code = {}
        for p_code, p_name, p_desc, perm_codes in PROFILES:
            profile = db.execute(select(Profile).where(Profile.code == p_code)).scalar_one_or_none()
            if not profile:
                profile = Profile(id=uuid.uuid4(), code=p_code, name=p_name, description=p_desc,
                                  is_system=True, is_active=True)
                db.add(profile)
                db.flush()
                print(f"+ Perfil creado: {p_code}")
            profile_by_code[p_code] = profile

            existing = {pp.permission_id for pp in db.execute(
                select(ProfilePermission).where(ProfilePermission.profile_id == profile.id)).scalars().all()}
            added = 0
            for perm_code in perm_codes:
                perm = permission_by_code.get(perm_code)
                if not perm:
                    print(f"  ADVERTENCIA: permiso '{perm_code}' no existe, omitiendo.")
                    continue
                if perm.id in existing:
                    continue
                db.add(ProfilePermission(id=uuid.uuid4(), profile_id=profile.id, permission_id=perm.id))
                added += 1
            print(f"  {p_code}: {added} permisos nuevos ({len(perm_codes)} en total)")
        db.commit()

        holding = db.execute(select(Holding)).scalars().first()
        if not holding:
            print("ERROR: no hay Holding — corré seed_initial_data.py primero.")
            sys.exit(1)

        def assign(user, profile_code):
            hu = db.execute(select(HoldingUser).where(
                HoldingUser.holding_id == holding.id, HoldingUser.user_id == user.id)).scalar_one_or_none()
            if not hu:
                hu = HoldingUser(id=uuid.uuid4(), holding_id=holding.id, user_id=user.id,
                                 role=profile_code, is_super_admin=False, is_active=True)
                db.add(hu)
                db.flush()
                print(f"  + HoldingUser creado para {user.email}")
            exists = db.execute(select(HoldingUserProfile).where(
                HoldingUserProfile.holding_user_id == hu.id,
                HoldingUserProfile.profile_id == profile_by_code[profile_code].id)).scalar_one_or_none()
            if exists:
                print(f"  {user.email} ya tiene '{profile_code}'")
                return
            db.add(HoldingUserProfile(id=uuid.uuid4(), holding_user_id=hu.id,
                                      profile_id=profile_by_code[profile_code].id, company_id=None))
            print(f"+ {user.email} -> {profile_code}")

        # ── Admins de transporte existentes ──────────────────────────────────
        for email, profile_code in EXISTING_ASSIGNMENTS:
            user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if not user:
                print(f"  ADVERTENCIA: usuario '{email}' no existe, omitiendo.")
                continue
            assign(user, profile_code)
        db.commit()

        # ── Usuarios de prueba ───────────────────────────────────────────────
        tc = db.execute(select(TransportCompany).where(TransportCompany.name == TEST_COMPANY_NAME)).scalar_one_or_none()
        if not tc:
            print(f"ERROR: no existe la empresa '{TEST_COMPANY_NAME}' — corré seed_demo_data.py primero.")
            sys.exit(1)

        for email, full_name, profile_code in TEST_USERS:
            user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if not user:
                initials = "".join(w[0].upper() for w in full_name.split()[-2:])
                user = User(id=uuid.uuid4(), email=email, full_name=full_name, avatar_initials=initials,
                            role=UserRole.CORP_ADMIN, hashed_password=hash_password(TEST_PASSWORD),
                            is_active=True, is_verified=True)
                db.add(user)
                db.flush()
                print(f"+ Usuario creado: {email}")
            tca = db.execute(select(TransportCompanyAdmin).where(
                TransportCompanyAdmin.user_id == user.id)).scalar_one_or_none()
            if not tca:
                db.add(TransportCompanyAdmin(id=uuid.uuid4(), transport_company_id=tc.id, user_id=user.id,
                                             is_primary=False, can_accept_routes=profile_code in ("transport_dispatcher",),
                                             can_manage_fleet=profile_code in ("transport_dispatcher",),
                                             can_view_billing=profile_code in ("transport_billing", "transport_viewer")))
                print(f"  + vinculado a {tc.name}")
            assign(user, profile_code)
        db.commit()

        print("\nListo. Usuarios de prueba (contraseña para todos: " + TEST_PASSWORD + "):")
        for email, full_name, profile_code in TEST_USERS:
            print(f"  {email:<34} {profile_code}")


if __name__ == "__main__":
    main()

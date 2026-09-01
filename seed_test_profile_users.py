"""
seed_test_profile_users.py — Crea un usuario de prueba por cada perfil del
modelo de seguridad, para poder probar manualmente qué puede y qué no puede
hacer cada uno en el portal warehouse.

Todos idempotentes (no duplica si ya existen), todos con la misma
contraseña de prueba, y todos con acceso a la misma Company (la primera
que encuentre en el holding — en la base actual, "LegalDocs Express LLC").

Perfiles creados:
  super_admin      — super.admin@muevo.app      (alcance: todo el holding)
  company_admin    — company.admin@muevo.app    (alcance: la company)
  warehouse_admin  — warehouse.admin@muevo.app  (alcance: la company)
  operator         — operator@muevo.app         (alcance: la company)
  viewer           — viewer@muevo.app           (alcance: la company)
  billing_manager  — billing.manager@muevo.app  (alcance: la company)

Uso:
    cd C:\\muevo-backend
    .venv\\Scripts\\python seed_test_profile_users.py
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
    User, UserRole, Holding, Company,
    HoldingUser, HoldingUserCompany, HoldingUserProfile, Profile,
)
from main import hash_password

engine = create_engine(DATABASE_URL, echo=False)

TEST_PASSWORD = "muevo123"

# code de perfil, email, nombre para mostrar, (can_operate, can_invoice, is_admin)
PROFILE_USERS = [
    ("super_admin",     "super.admin@muevo.app",     "Test Super Admin",     (True,  True,  True)),
    ("company_admin",   "company.admin@muevo.app",   "Test Company Admin",  (True,  True,  True)),
    ("warehouse_admin", "warehouse.admin@muevo.app", "Test Warehouse Admin",(True,  False, True)),
    ("operator",        "operator@muevo.app",        "Test Operator",       (True,  False, False)),
    ("viewer",          "viewer@muevo.app",          "Test Viewer",         (False, False, False)),
    ("billing_manager", "billing.manager@muevo.app", "Test Billing Manager",(False, True,  False)),
]


def main():
    with Session(engine) as db:
        holding = db.execute(select(Holding)).scalars().first()
        company = db.execute(select(Company).where(Company.holding_id == holding.id)).scalars().first()
        if not holding or not company:
            print("ERROR: no hay Holding/Company en la base — corré seed_initial_data.py primero.")
            sys.exit(1)
        print(f"Holding: {holding.name} | Company: {company.name}\n")

        profile_by_code = {p.code: p for p in db.execute(select(Profile)).scalars().all()}

        for profile_code, email, name, (can_operate, can_invoice, is_admin) in PROFILE_USERS:
            profile = profile_by_code.get(profile_code)
            if not profile:
                print(f"ADVERTENCIA: perfil '{profile_code}' no existe (corré seed_security_extensions.py). Omitiendo {email}.")
                continue

            user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if not user:
                initials = "".join(w[0].upper() for w in name.split()[-2:])
                user = User(
                    id=uuid.uuid4(), email=email, full_name=name,
                    avatar_initials=initials, role=UserRole.CORP_ADMIN,
                    hashed_password=hash_password(TEST_PASSWORD),
                    is_active=True, is_verified=True,
                )
                db.add(user)
                db.flush()
                print(f"+ Usuario creado: {email}")
            else:
                print(f"  Usuario '{email}' ya existe, omitiendo creación.")

            hu = db.execute(
                select(HoldingUser).where(HoldingUser.holding_id == holding.id, HoldingUser.user_id == user.id)
            ).scalar_one_or_none()
            if not hu:
                hu = HoldingUser(
                    id=uuid.uuid4(), holding_id=holding.id, user_id=user.id,
                    role=profile_code, is_super_admin=(profile_code == "super_admin"), is_active=True,
                )
                db.add(hu)
                db.flush()
                print(f"  + HoldingUser creado (role={profile_code})")

            huc = db.execute(
                select(HoldingUserCompany).where(
                    HoldingUserCompany.holding_user_id == hu.id, HoldingUserCompany.company_id == company.id
                )
            ).scalar_one_or_none()
            if not huc:
                db.add(HoldingUserCompany(
                    id=uuid.uuid4(), holding_user_id=hu.id, company_id=company.id,
                    can_view=True, can_operate=can_operate, can_invoice=can_invoice, is_admin=is_admin,
                ))
                print(f"  + Acceso a '{company.name}' otorgado")

            scope_company_id = None if profile_code == "super_admin" else company.id
            hup = db.execute(
                select(HoldingUserProfile).where(HoldingUserProfile.holding_user_id == hu.id)
            ).scalar_one_or_none()
            if not hup:
                db.add(HoldingUserProfile(
                    id=uuid.uuid4(), holding_user_id=hu.id, profile_id=profile.id,
                    company_id=scope_company_id,
                ))
                scope_label = "todo el holding" if scope_company_id is None else company.name
                print(f"  + Perfil '{profile_code}' asignado (alcance: {scope_label})")
            else:
                print(f"  Ya tenía un perfil asignado, omitiendo.")

            db.commit()
            print()

        print("Listo. Usuarios de prueba (contraseña para todos: " + TEST_PASSWORD + "):")
        for _, email, name, _ in PROFILE_USERS:
            print(f"  {email:<30} {name}")


if __name__ == "__main__":
    main()

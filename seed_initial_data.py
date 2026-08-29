"""
seed_initial_data.py — Bootstrap de la base de datos en un equipo nuevo.

Los catálogos base (service_types, vehicle_types) ya vienen sembrados por
las migraciones 012_service_types.py / 013_vehicle_types.py — no hace
falta ningún seed aparte para eso.

Lo que SÍ faltaba, porque hasta ahora se había creado a mano en el equipo
original sin quedar en ningún script ni migración, es la estructura
holding → company → warehouse → usuario admin. Este script la crea de
forma idempotente (no duplica si ya existe):

  - 1 Holding ("single_entity")
  - 1 Company ("LegalDocs Express LLC") perteneciente a ese holding
  - 1 Warehouse de esa company (Orlando, FL)
  - Usuario admin carlos@muevo.app / muevo123, como HoldingUser
    (super_admin) con acceso completo a la company

Después de correr esto, seed_demo_data.py y seed_incident_types.py ya
pueden ejecutarse (ambos asumen que ya existe al menos una Company).

Uso:
    cd C:\\muevo-backend
    .venv\\Scripts\\python seed_initial_data.py
"""

import sys, os, uuid
sys.path.insert(0, os.path.dirname(__file__))

# Windows: la consola por defecto (cp1252) no puede imprimir ✓/✅ — forzar UTF-8.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:Semeolvido.01@localhost:5432/muevodb")

from models import (
    Holding, Company, Warehouse, User, UserRole,
    HoldingUser, HoldingUserCompany,
)
from main import hash_password

engine = create_engine(DATABASE_URL, echo=False)

ADMIN_EMAIL    = "carlos@muevo.app"
ADMIN_PASSWORD = "muevo123"
ADMIN_NAME     = "Carlos Rodriguez"
COMPANY_NAME   = "LegalDocs Express LLC"


def main():
    with Session(engine) as db:
        # ── 1. Holding ─────────────────────────────────────────────────────────
        holding = db.execute(select(Holding).where(Holding.name == COMPANY_NAME)).scalar_one_or_none()
        if not holding:
            holding = Holding(
                id=uuid.uuid4(), name=COMPANY_NAME, holding_type="single_entity",
                billing_mode="per_company", country="US", is_active=True,
            )
            db.add(holding)
            db.flush()
            print(f"✓ Holding creado: {holding.name}")
        else:
            print(f"  Holding '{COMPANY_NAME}' ya existe, omitiendo.")

        # ── 2. Company ─────────────────────────────────────────────────────────
        company = db.execute(select(Company).where(Company.name == COMPANY_NAME)).scalar_one_or_none()
        if not company:
            company = Company(
                id=uuid.uuid4(), holding_id=holding.id, is_primary_company=True,
                name=COMPANY_NAME, industry="legal",
                contact_name=ADMIN_NAME, contact_email=ADMIN_EMAIL,
                city="Orlando", state="FL", country="US",
                allowed_modes=["mensajeria", "logistica", "paqueteria"],
                is_active=True,
            )
            db.add(company)
            db.flush()
            print(f"✓ Company creada: {company.name}")
        else:
            print(f"  Company '{COMPANY_NAME}' ya existe, omitiendo.")

        # ── 3. Warehouse ───────────────────────────────────────────────────────
        wh = db.execute(select(Warehouse).where(Warehouse.company_id == company.id)).scalar_one_or_none()
        if not wh:
            wh = Warehouse(
                id=uuid.uuid4(), company_id=company.id, code="ORL-01",
                name=f"{COMPANY_NAME} — Depósito Central", warehouse_type="distribution",
                address_line1="200 S Orange Ave", city="Orlando", state="FL",
                zip_code="32801", country="US", is_active=True, is_origin_default=True,
            )
            db.add(wh)
            print(f"✓ Warehouse creado: {wh.name}")
        else:
            print(f"  Warehouse de '{COMPANY_NAME}' ya existe, omitiendo.")
        db.commit()

        # ── 4. Usuario admin ───────────────────────────────────────────────────
        user = db.execute(select(User).where(User.email == ADMIN_EMAIL)).scalar_one_or_none()
        if not user:
            initials = "".join(w[0].upper() for w in ADMIN_NAME.split()[:2])
            user = User(
                id=uuid.uuid4(), email=ADMIN_EMAIL, full_name=ADMIN_NAME,
                avatar_initials=initials, role=UserRole.CORP_ADMIN,
                hashed_password=hash_password(ADMIN_PASSWORD),
                is_active=True, is_verified=True,
            )
            db.add(user)
            db.flush()
            print(f"✓ Usuario creado: {ADMIN_EMAIL} / {ADMIN_PASSWORD}")
        else:
            print(f"  Usuario '{ADMIN_EMAIL}' ya existe, omitiendo.")
        db.commit()

        # ── 5. HoldingUser (super_admin) ──────────────────────────────────────
        hu = db.execute(
            select(HoldingUser).where(HoldingUser.holding_id == holding.id, HoldingUser.user_id == user.id)
        ).scalar_one_or_none()
        if not hu:
            hu = HoldingUser(
                id=uuid.uuid4(), holding_id=holding.id, user_id=user.id,
                role="super_admin", is_super_admin=True, is_active=True,
                can_create_companies=True, can_view_consolidated_billing=True,
            )
            db.add(hu)
            db.flush()
            print(f"✓ HoldingUser creado (super_admin) para {ADMIN_EMAIL}")
        else:
            print(f"  HoldingUser de '{ADMIN_EMAIL}' ya existe, omitiendo.")
        db.commit()

        # ── 6. Acceso a la company ─────────────────────────────────────────────
        huc = db.execute(
            select(HoldingUserCompany).where(
                HoldingUserCompany.holding_user_id == hu.id,
                HoldingUserCompany.company_id == company.id,
            )
        ).scalar_one_or_none()
        if not huc:
            db.add(HoldingUserCompany(
                id=uuid.uuid4(), holding_user_id=hu.id, company_id=company.id,
                can_view=True, can_operate=True, can_invoice=True, is_admin=True,
            ))
            print(f"✓ Acceso completo otorgado a {ADMIN_EMAIL} sobre '{COMPANY_NAME}'")
        else:
            print(f"  Acceso de '{ADMIN_EMAIL}' a '{COMPANY_NAME}' ya existe, omitiendo.")
        db.commit()

        print("\n✅ Listo. Ya podés entrar al portal warehouse con:")
        print(f"   {ADMIN_EMAIL} / {ADMIN_PASSWORD}")


if __name__ == "__main__":
    main()

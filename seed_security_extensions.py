"""
seed_security_extensions.py — Extiende el catálogo RBAC existente y asigna
perfiles a los usuarios ya creados.

Contexto: el sistema de permisos (modules/actions/permissions/profiles/
profile_permissions) ya existía en la base, sembrado con 8 módulos y 6
perfiles, pero:
  (a) no cubría las funcionalidades agregadas después (Incidencias,
      Clientes, Negociación+Tarifas, Recepción, Inventario, Mensajes), y
  (b) holding_user_profiles tenía 0 filas — ningún usuario tenía un
      perfil realmente asignado.

Este script, de forma idempotente (no duplica si ya existe):
  1. Agrega 6 módulos nuevos: incidents, clients, pricing, receptions,
     inventory, messages — cada uno solo con las acciones que tienen
     sentido real en el sistema (no las 7 genéricas para todos).
  2. Crea los permisos (módulo x acción) correspondientes.
  3. Le asigna esos permisos nuevos a los 6 perfiles existentes,
     replicando el patrón ya usado en los módulos existentes (ej.
     company_admin y warehouse_admin espejan a routes/batches; operator
     nunca tiene delete; viewer solo view).
  4. Asigna un HoldingUserProfile a cada HoldingUser que todavía no
     tenga ninguno (por default, super_admin si is_super_admin=True,
     si no operator) — deja el sistema listo para que require_permission()
     tenga algo real que consultar.

NO TOCA los permisos ya existentes de los 8 módulos originales — esos
quedan tal cual estaban configurados.

Uso:
    cd C:\\muevo-backend
    .venv\\Scripts\\python seed_security_extensions.py
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

from models import Module, Action, Permission, Profile, ProfilePermission, HoldingUser, HoldingUserProfile

engine = create_engine(DATABASE_URL, echo=False)

# ── 1. Módulos nuevos + sus acciones reales ──────────────────────────────────
NEW_MODULES = [
    # code,         name,                    acciones reales
    ("incidents",  "Incidencias",             ["view", "create", "edit", "delete"]),
    ("clients",    "Clientes",                ["view", "create", "edit", "delete"]),
    ("pricing",    "Precios y negociación",   ["view", "create", "edit", "approve", "cancel"]),
    ("receptions", "Recepción de mercancía",  ["view", "create", "edit", "approve", "delete"]),
    ("inventory",  "Inventario",              ["view", "create", "edit", "delete"]),
    ("messages",   "Mensajes",                ["view", "create", "edit"]),
    ("audit",      "Auditoría",               ["view"]),
]

# ── 2. Qué acciones tiene cada perfil en cada módulo nuevo ───────────────────
# Replica el patrón ya usado en routes/batches: company_admin y
# warehouse_admin con acceso operativo completo, operator sin delete/cancel
# fuertes, viewer solo view, billing_manager solo lo financiero (pricing).
PROFILE_GRANTS = {
    "super_admin":     {"incidents": "vced", "clients": "vced", "pricing": "vceax",
                         "receptions": "vcead", "inventory": "vced", "messages": "vce",
                         "audit": "v"},
    "company_admin":   {"incidents": "vced", "clients": "vced", "pricing": "vceax",
                         "receptions": "vcead", "inventory": "vced", "messages": "vce"},
    "warehouse_admin": {"incidents": "vced", "clients": "vced", "pricing": "vceax",
                         "receptions": "vcead", "inventory": "vced", "messages": "vce"},
    "operator":        {"incidents": "vce", "clients": "vce", "pricing": "vceax",
                         "receptions": "vcea", "inventory": "vce", "messages": "v"},
    "viewer":          {"incidents": "v", "clients": "v", "pricing": "v",
                         "receptions": "v", "inventory": "v", "messages": "v"},
    "billing_manager": {"pricing": "v"},
}
ACTION_LETTER = {"v": "view", "c": "create", "e": "edit", "d": "delete", "a": "approve", "x": "cancel"}


def main():
    with Session(engine) as db:
        # ── Módulos ────────────────────────────────────────────────────────
        module_by_code = {}
        for code, name, _actions in NEW_MODULES:
            m = db.execute(select(Module).where(Module.code == code)).scalar_one_or_none()
            if not m:
                m = Module(id=uuid.uuid4(), code=code, name=name,
                           sort_order=100 + len(module_by_code))
                db.add(m)
                db.flush()
                print(f"+ Module creado: {code}")
            else:
                print(f"  Module '{code}' ya existe, omitiendo.")
            module_by_code[code] = m
        db.commit()

        # ── Acciones (ya existen todas — solo las referenciamos) ────────────
        action_by_code = {
            a.code: a for a in db.execute(select(Action)).scalars().all()
        }

        # ── Permisos ─────────────────────────────────────────────────────────
        permission_by_code = {
            p.code: p for p in db.execute(select(Permission)).scalars().all()
        }
        for code, name, actions in NEW_MODULES:
            module = module_by_code[code]
            for action_code in actions:
                perm_code = f"{code}.{action_code}"
                if perm_code in permission_by_code:
                    print(f"  Permission '{perm_code}' ya existe, omitiendo.")
                    continue
                action = action_by_code[action_code]
                perm = Permission(
                    id=uuid.uuid4(), code=perm_code,
                    name=f"{name} — {action.name}",
                    module_id=module.id, action_id=action.id, is_active=True,
                )
                db.add(perm)
                db.flush()
                permission_by_code[perm_code] = perm
                print(f"+ Permission creado: {perm_code}")
        db.commit()

        # ── Perfil -> permisos nuevos ────────────────────────────────────────
        profile_by_code = {
            p.code: p for p in db.execute(select(Profile)).scalars().all()
        }
        existing_pp = {
            (pp.profile_id, pp.permission_id)
            for pp in db.execute(select(ProfilePermission)).scalars().all()
        }
        added_pp = 0
        for profile_code, module_grants in PROFILE_GRANTS.items():
            profile = profile_by_code.get(profile_code)
            if not profile:
                print(f"  ADVERTENCIA: perfil '{profile_code}' no existe, omitiendo sus permisos nuevos.")
                continue
            for module_code, letters in module_grants.items():
                for letter in letters:
                    action_code = ACTION_LETTER[letter]
                    perm_code = f"{module_code}.{action_code}"
                    perm = permission_by_code.get(perm_code)
                    if not perm:
                        continue
                    key = (profile.id, perm.id)
                    if key in existing_pp:
                        continue
                    db.add(ProfilePermission(id=uuid.uuid4(), profile_id=profile.id, permission_id=perm.id))
                    existing_pp.add(key)
                    added_pp += 1
        db.commit()
        print(f"+ {added_pp} relaciones perfil-permiso nuevas creadas")

        # ── Asignar perfil a HoldingUsers que todavía no tengan ninguno ──────
        holding_users = db.execute(select(HoldingUser)).scalars().all()
        assigned_ids = {
            hup.holding_user_id
            for hup in db.execute(select(HoldingUserProfile)).scalars().all()
        }
        super_admin_profile = profile_by_code.get("super_admin")
        operator_profile    = profile_by_code.get("operator")
        for hu in holding_users:
            if hu.id in assigned_ids:
                print(f"  HoldingUser {hu.id} ya tiene perfil asignado, omitiendo.")
                continue
            default_profile = super_admin_profile if hu.is_super_admin else operator_profile
            if not default_profile:
                continue
            db.add(HoldingUserProfile(
                id=uuid.uuid4(), holding_user_id=hu.id, profile_id=default_profile.id,
                company_id=None,  # alcance: todo el holding
            ))
            print(f"+ HoldingUser {hu.id} -> perfil '{default_profile.code}' (todo el holding)")
        db.commit()

        print("\nListo. Catálogo extendido y perfiles asignados.")


if __name__ == "__main__":
    main()

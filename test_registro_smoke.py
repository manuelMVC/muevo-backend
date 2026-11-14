"""
Prueba de humo del registro de empresas de transporte y de warehouses (solicitud, aprobación,
rechazo, login, servicios habilitados y límite de intentos). Usa la base real y limpia lo que crea.

    cd C:\\Claude\\muevo-backend
    .venv\\Scripts\\python test_registro_smoke.py
"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import main
from fastapi.testclient import TestClient
from main import app, engine
from sqlalchemy import text
from sqlalchemy.orm import Session

client = TestClient(app)
main._REGISTER_ATTEMPTS.clear()


def check(label, cond, extra=""):
    print(f"[{'OK' if cond else 'FALLO'}] {label} {extra}")
    if not cond:
        raise AssertionError(label + " " + extra)


def login(email, pw="muevo123"):
    r = client.post("/api/v1/auth/token", data={"username": email, "password": pw})
    check(f"login {email}", r.status_code == 200, r.text[:60])
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()


stamp = str(int(time.time()))
T_EMAIL, W_EMAIL, T2_EMAIL = (f"smoke.t.{stamp}@example.test", f"smoke.w.{stamp}@example.test", f"smoke.t2.{stamp}@example.test")
T_EIN, W_EIN, T2_EIN = f"61-{stamp[-7:]}", f"62-{stamp[-7:]}", f"63-{stamp[-7:]}"
NASTY = "Smoke <img src=x onerror=alert(1)> LLC"

muevo, _ = login("admin@muevo.app")


def transport_payload(email, ein, **over):
    base = {"company_name": NASTY, "legal_name": NASTY, "tax_id": ein, "dot_number": "9999991", "mc_number": None,
            "insurance_policy_number": "POL-1", "insurance_expires": "2031-01-31", "service_modes": ["mensajeria"], "fleet_size": 2,
            "contact_name": "Ana", "contact_email": email, "contact_phone": "407-555-0100", "address_line1": "1 St",
            "city": "Orlando", "state": "FL", "zip_code": "32801", "admin_full_name": "Ana Smoke", "admin_email": email,
            "admin_password": "ClaveSegura1"}
    base.update(over)
    return base


def warehouse_payload(email, ein, **over):
    base = {"company_name": NASTY, "legal_name": NASTY, "tax_id": ein, "industry": "retail",
            "service_types": ["mensajeria", "paqueteria"], "estimated_monthly_stops": 100, "contact_name": "Luz",
            "contact_email": email, "contact_phone": "407-555-0101", "address_line1": "2 St", "city": "Orlando", "state": "FL",
            "zip_code": "32801", "warehouse_name": "Bodega Smoke", "warehouse_address_line1": "3 St", "warehouse_city": "Orlando",
            "warehouse_state": "FL", "warehouse_zip_code": "32809", "warehouse_docks_count": 1, "admin_full_name": "Luz Smoke",
            "admin_email": email, "admin_password": "ClaveSegura1"}
    base.update(over)
    return base


print("\n-- Transporte --")
r = client.post("/api/v1/transport/register", json=transport_payload(T_EMAIL, T_EIN))
check("solicitud creada", r.status_code == 201, r.text[:90])
t_app = r.json()["id"]
check("EIN duplicado -> 409", client.post("/api/v1/transport/register", json=transport_payload(T2_EMAIL, T_EIN)).status_code == 409)
check("seguro vencido -> 422", client.post("/api/v1/transport/register", json=transport_payload(T2_EMAIL, T2_EIN, insurance_expires="2020-01-01")).status_code == 422)
r = client.post(f"/api/v1/admin/transport-applications/{t_app}/approve", headers=muevo)
check("aprobada (el correo con un nombre hostil no rompe nada)", r.status_code == 200, r.text[:100])
tc_id, t_user = r.json()["transport_company_id"], r.json()["user_id"]
h, info = login(T_EMAIL, "ClaveSegura1")
check("el admin entra al portal de transporte", info["portal"] == "transport")
check("y ve la facturación con su perfil", client.get("/api/v1/transport/billing", headers=h).status_code == 200)
r = client.post("/api/v1/transport/register", json=transport_payload(T2_EMAIL, T2_EIN))
t2_app = r.json()["id"]
r = client.post(f"/api/v1/admin/transport-applications/{t2_app}/reject", json={"reason": "Motivo <b>de prueba</b>"}, headers=muevo)
check("rechazada con motivo", r.status_code == 200)

print("\n-- Warehouse --")
r = client.post("/api/v1/warehouse/register", json=warehouse_payload(W_EMAIL, W_EIN, service_types=["xx"]))
check("servicio inexistente -> 422", r.status_code == 422)
r = client.post("/api/v1/warehouse/register", json=warehouse_payload(W_EMAIL, W_EIN))
check("solicitud creada", r.status_code == 201, r.text[:90])
w_app = r.json()["id"]
r = client.post(f"/api/v1/admin/company-applications/{w_app}/approve", headers=muevo)
check("aprobada", r.status_code == 200, r.text[:100])
ids = r.json()
h, info = login(W_EMAIL, "ClaveSegura1")
H = {**h, "X-Company-ID": ids["company_id"]}
check("entra al portal warehouse con su compañía", info["portal"] == "warehouse" and info["auto_company"]["id"] == ids["company_id"])
sel = client.get("/api/v1/warehouse/company/service-types", headers=H).json()["selected"]
check("tiene los 2 servicios elegidos", sel == ["mensajeria", "paqueteria"], str(sel))
stop = lambda n, lat, lng: {"destino": n, "direccion": "1 St", "codigo_postal": "32801", "lat": lat, "lng": lng, "peso_lbs": 5, "volumen_ft3": 1, "hora_limite": "17:00"}
base = {"vehicle_type": "furgoneta", "peso_max_lbs": 2000, "volumen_max_ft3": 200, "max_stops_per_route": 12, "paradas": [stop("A", 28.54, -81.38)]}
check("servicio no habilitado -> 400", client.post("/api/v1/warehouse/batches/plan", json={**base, "service_type": "logistica"}, headers=H).status_code == 400)
check("servicio habilitado -> 200", client.post("/api/v1/warehouse/batches/plan", json={**base, "service_type": "paqueteria"}, headers=H).status_code == 200)
check("aislamiento: no entra a otra compañía", client.get("/api/v1/warehouse/batches", headers={**h, "X-Company-ID": str(
    Session(engine).execute(text("select id from companies where name='LegalDocs Express LLC'")).scalar())}).status_code == 403)

print("\n-- Notificaciones y auditoría --")
with Session(engine) as db:
    leaks = db.execute(text("select count(*) from audit_logs where created_at > now() - interval '15 minutes' and changes::text like '%$2b$%'")).scalar()
check("0 hashes de contraseña en audit_logs", leaks == 0, str(leaks))

print("\n-- Límite de intentos --")
main._REGISTER_ATTEMPTS.clear()
codes = [client.post("/api/v1/transport/register", json=transport_payload(f"rl{i}.{stamp}@example.test", f"64-{i}{stamp[-6:]}", insurance_expires="2020-01-01")).status_code for i in range(12)]
check("el intento 11 responde 429", codes[:10] == [422] * 10 and codes[10:] == [429, 429], str(codes))
main._REGISTER_ATTEMPTS.clear()

print("\nTODO OK\n--- Limpieza ---")
with Session(engine) as db:
    users = [r[0] for r in db.execute(text("select id from users where email like 'smoke.%'")).all()]
    hu = [r[0] for r in db.execute(text("select id from holding_users where user_id = any(cast(:u as uuid[]))"), {"u": users}).all()]
    wh_ids = [str(r[0]) for r in db.execute(text("select id from warehouses where company_id=:c"), {"c": ids["company_id"]}).all()]
    apps = [t_app, t2_app, w_app]
    db.execute(text("delete from notifications where entity_id = any(cast(:a as text[])) or user_id = any(cast(:u as uuid[]))"), {"a": apps, "u": users})
    db.execute(text("delete from holding_user_profiles where holding_user_id = any(cast(:h as uuid[]))"), {"h": hu})
    db.execute(text("delete from holding_user_companies where holding_user_id = any(cast(:h as uuid[]))"), {"h": hu})
    db.execute(text("delete from holding_users where id = any(cast(:h as uuid[]))"), {"h": hu})
    db.execute(text("delete from transport_company_admins where user_id = any(cast(:u as uuid[]))"), {"u": users})
    db.execute(text("update transport_applications set transport_company_id=null where id in (:a,:b)"), {"a": t_app, "b": t2_app})
    db.execute(text("update company_applications set company_id=null where id=:a"), {"a": w_app})
    db.execute(text("delete from transport_companies where id=:t"), {"t": tc_id})
    db.execute(text("delete from audit_logs where company_id=:c"), {"c": ids["company_id"]})
    db.execute(text("delete from warehouses where company_id=:c"), {"c": ids["company_id"]})
    db.execute(text("delete from companies where id=:c"), {"c": ids["company_id"]})
    db.execute(text("delete from holdings where id=:h"), {"h": ids["holding_id"]})
    db.execute(text("delete from transport_applications where id in (:a,:b)"), {"a": t_app, "b": t2_app})
    db.execute(text("delete from company_applications where id=:a"), {"a": w_app})
    db.execute(text("delete from audit_logs where record_id = any(cast(:i as text[])) or changed_by_user_id = any(cast(:u as uuid[]))"),
               {"i": [str(x) for x in users + hu] + wh_ids + apps + [tc_id, ids["company_id"], ids["holding_id"]], "u": users})
    db.execute(text("delete from users where id = any(cast(:u as uuid[]))"), {"u": users})
    db.execute(text("ALTER TABLE profile_change_history DISABLE TRIGGER trg_profile_change_history_rows"))
    db.execute(text("delete from profile_change_history where target_user_email like 'smoke.%'"))
    db.execute(text("ALTER TABLE profile_change_history ENABLE TRIGGER trg_profile_change_history_rows"))
    db.commit()
    left = db.execute(text("select (select count(*) from users where email like 'smoke.%'), (select count(*) from companies where name like 'Smoke%'), (select count(*) from transport_companies where name like 'Smoke%'), (select tgenabled::text from pg_trigger where tgname='trg_profile_change_history_rows')")).one()
print("   datos de prueba eliminados; restos y trigger:", tuple(left))

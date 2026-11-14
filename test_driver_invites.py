"""
Invitación, registro, aprobación y suspensión de conductores (usa la base real y limpia lo que crea).

    cd C:\Claude\muevo-backend
    .venv\Scripts\python test_driver_invites.py
"""
import sys, os, time, re
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import main
from fastapi.testclient import TestClient
from main import app, engine
from sqlalchemy.orm import Session
from sqlalchemy import text

client = TestClient(app)
main._REGISTER_ATTEMPTS.clear()


def check(label, cond, extra=""):
    print(f"[{'OK' if cond else 'FALLO'}] {label} {extra}")
    if not cond:
        raise AssertionError(label + " " + extra)


def hdr(email, pw="muevo123"):
    r = client.post("/api/v1/auth/token", data={"username": email, "password": pw})
    check(f"login {email}", r.status_code == 200, r.text[:60])
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()


stamp = str(int(time.time()))
E1, E2 = f"test.drv.1.{stamp}@example.test", f"test.drv.2.{stamp}@example.test"

admin, _ = hdr("transport@muevo.app")             # transport_admin de Rapid Courier
disp, _ = hdr("transport.dispatcher@muevo.app")   # fleet.view + fleet.edit, sin fleet.create
bill, _ = hdr("transport.billing@muevo.app")
sun, _ = hdr("sunshine@muevo.app")                # otra empresa

print("\n-- Invitar --")
r = client.post("/api/v1/transport/driver-invitations", json={"email": "no-es-correo"}, headers=admin)
check("correo inválido -> 422", r.status_code == 422, str(r.status_code))
r = client.post("/api/v1/transport/driver-invitations", json={"email": "driver@muevo.app"}, headers=admin)
check("correo de un usuario existente -> 409", r.status_code == 409, str(r.status_code))
check("despachador (sin fleet.create) NO invita (403)", client.post("/api/v1/transport/driver-invitations", json={"email": E1}, headers=disp).status_code == 403)
check("facturación NO invita (403)", client.post("/api/v1/transport/driver-invitations", json={"email": E1}, headers=bill).status_code == 403)
check("sin login -> 401", client.post("/api/v1/transport/driver-invitations", json={"email": E1}).status_code == 401)
r = client.post("/api/v1/transport/driver-invitations", json={"email": E1.upper(), "full_name": "Dani Test"}, headers=admin)
check("admin invita", r.status_code == 201, r.text[:120])
inv1 = r.json()
tok1 = inv1["invite_url"].split("token=")[1]
check("el enlace lleva el token y la página de registro", "/register-driver.html?token=" in inv1["invite_url"] and len(tok1) >= 40)
check("el correo se normaliza a minúsculas", inv1["email"] == E1)
with Session(engine) as db:
    row = db.execute(text("select token_hash from driver_invitations where id=:i"), {"i": inv1["id"]}).scalar()
check("en la base solo está el hash (no el token)", row != tok1 and len(row) == 64 and tok1 not in row)
lst = client.get("/api/v1/transport/driver-invitations", headers=admin).json()
check("aparece en la lista como pendiente, sin token", any(x["id"] == inv1["id"] and x["status"] == "pending" for x in lst) and "invite_url" not in str(lst) and "token" not in str(lst))
check("otra empresa NO la ve", all(x["id"] != inv1["id"] for x in client.get("/api/v1/transport/driver-invitations", headers=sun).json()))

print("\n-- Reenviar reemplaza la anterior --")
r = client.post("/api/v1/transport/driver-invitations", json={"email": E1, "full_name": "Dani Test"}, headers=admin)
inv1b = r.json(); tok1b = inv1b["invite_url"].split("token=")[1]
check("la nueva se crea", r.status_code == 201 and tok1b != tok1)
check("el enlace viejo quedó revocado (410)", client.get(f"/api/v1/driver-invitations/{tok1}").status_code == 410)
check("el nuevo es válido", client.get(f"/api/v1/driver-invitations/{tok1b}").status_code == 200)

print("\n-- Enlace público --")
check("token inexistente -> 404", client.get("/api/v1/driver-invitations/xxxxxxxx").status_code == 404)
info = client.get(f"/api/v1/driver-invitations/{tok1b}").json()
check("muestra empresa, correo y nombre invitado", info["company_name"] == "Rapid Courier Orlando LLC" and info["email"] == E1 and info["full_name"] == "Dani Test", str(info))

print("\n-- Aceptar --")
def acc(**over):
    base = {"full_name": "Dani Test", "phone": "407-555-0140", "password": "ClaveSegura1", "license_state": "FL", "license_expires": "2031-05-20"}
    base.update(over)
    return base
for label, over in [("licencia vencida", {"license_expires": "2020-01-01"}), ("estado inexistente", {"license_state": "ZZ"}),
                    ("contraseña corta", {"password": "123"}), ("sin teléfono", {"phone": ""})]:
    check(f"rechaza: {label}", client.post(f"/api/v1/driver-invitations/{tok1b}/accept", json=acc(**over)).status_code == 422)
r = client.post(f"/api/v1/driver-invitations/{tok1b}/accept", json=acc(email="otro@example.test"))
check("el correo sale de la invitación, no del formulario", r.status_code == 201, r.text[:100])
check("devuelve pendiente de aprobación", r.json()["status"] == "pending_approval")
check("reusar el enlace -> 410", client.post(f"/api/v1/driver-invitations/{tok1b}/accept", json=acc()).status_code == 410)
check("consultar un enlace usado -> 410", client.get(f"/api/v1/driver-invitations/{tok1b}").status_code == 410)

with Session(engine) as db:
    d = db.execute(text("""select u.email, u.role::text, d.is_approved, d.transport_company_id is not null, c.status::text, c.cert_type,
                                  c.issuing_authority from users u join drivers d on d.user_id=u.id left join certifications c on c.driver_id=d.id where u.email=:e"""), {"e": E1}).one()
    other = db.execute(text("select count(*) from users where email='otro@example.test'")).scalar()
check("usuario conductor, con su empresa, SIN aprobar", d[1] == "driver" and d[2] is False and d[3] is True, str(d))
check("licencia registrada como pendiente", d[4] == "pending" and d[5] == "driver_license" and d[6] == "Florida", str(d[4:]))
check("no se creó ningún usuario con el correo del formulario", other == 0)
with Session(engine) as db:
    n = db.execute(text("select count(*) from notifications n join users u on u.id=n.user_id where n.type='driver_registered' and n.created_at > now() - interval '5 minutes' and u.email='transport@muevo.app'")).scalar()
check("la empresa recibió el aviso", n >= 1, str(n))

print("\n-- Conductor pendiente --")
dh, dl = hdr(E1, "ClaveSegura1")
check("puede iniciar sesión (portal conductor)", dl["portal"] == "driver", str(dl["portal"]))
r = client.get("/api/v1/routes", headers=dh)
check("NO ve rutas ni precios hasta ser aprobado", r.status_code == 200 and r.json() == [], r.text[:80])
drivers = client.get("/api/v1/transport/drivers", headers=admin).json()
new = next(x for x in drivers if x["email"] == E1)
check("la empresa lo ve pendiente, con licencia", new["is_approved"] is False and new["license_expires"] == "2031-05-20", str(new))
check("otra empresa NO lo ve", all(x["email"] != E1 for x in client.get("/api/v1/transport/drivers", headers=sun).json()))
routes = client.get("/api/v1/transport/routes", headers=admin).json()
cand = next((x for x in routes if x["status"] in ("assigned", "published")), None)
check("hay una ruta para probar la asignación", cand is not None)
with Session(engine) as db:
    orig = db.execute(text("select status::text, vehicle_id, driver_id from route_headers where id=:r"), {"r": cand["id"]}).one()
    db.execute(text("update route_headers set status='published', vehicle_id=null, driver_id=null where id=:r"), {"r": cand["id"]})
    db.commit()
veh = next(v for v in client.get("/api/v1/transport/fleet", headers=admin).json() if not v["active_route_id"])["id"]
r = client.post(f"/api/v1/transport/routes/{cand['id']}/accept?vehicle_id={veh}&driver_id={new['id']}", headers=admin)
check("NO se le puede asignar una ruta sin aprobar (400)", r.status_code == 400 and "aprobado" in r.text, r.text[:90])
r = client.post(f"/api/v1/routes/{cand['id']}/accept", headers=dh)
check("tampoco puede tomar una ruta él mismo (403)", r.status_code == 403, str(r.status_code))

print("\n-- Aprobar --")
check("facturación NO aprueba (403)", client.post(f"/api/v1/transport/drivers/{new['id']}/approve", headers=bill).status_code == 403)
check("otra empresa NO lo aprueba (404)", client.post(f"/api/v1/transport/drivers/{new['id']}/approve", headers=sun).status_code == 404)
r = client.post(f"/api/v1/transport/drivers/{new['id']}/approve", headers=admin)
check("la empresa lo aprueba", r.status_code == 200 and r.json()["status"] == "approved", r.text[:80])
r = client.post(f"/api/v1/transport/routes/{cand['id']}/accept?vehicle_id={veh}&driver_id={new['id']}", headers=admin)
check("ahora sí se le asigna la ruta", r.status_code == 200, r.text[:100])
mine = client.get("/api/v1/routes", headers=dh).json()
check("y ve las rutas de su empresa", len(mine) >= 1, f"n={len(mine)}")

print("\n-- Suspender --")
r = client.post(f"/api/v1/transport/drivers/{new['id']}/suspend", headers=admin)
check("la empresa lo suspende", r.status_code == 200)
check("deja de ver rutas", client.get("/api/v1/routes", headers=dh).json() == [])
check("el despachador (tiene fleet.edit) puede volver a aprobarlo", client.post(f"/api/v1/transport/drivers/{new['id']}/approve", headers=disp).status_code == 200)
check("y entonces vuelve a ver rutas", len(client.get("/api/v1/routes", headers=dh).json()) >= 1)
client.post(f"/api/v1/transport/drivers/{new['id']}/suspend", headers=admin)
check("otra empresa NO lo suspende (404)", client.post(f"/api/v1/transport/drivers/{new['id']}/suspend", headers=sun).status_code == 404)

print("\n-- Revocar y vencer --")
r = client.post("/api/v1/transport/driver-invitations", json={"email": E2}, headers=admin)
inv2 = r.json(); tok2 = inv2["invite_url"].split("token=")[1]
check("otra empresa NO revoca (404)", client.post(f"/api/v1/transport/driver-invitations/{inv2['id']}/revoke", headers=sun).status_code == 404)
check("el despachador (fleet.edit) puede revocar", client.post(f"/api/v1/transport/driver-invitations/{inv2['id']}/revoke", headers=disp).status_code == 200)
check("revocada -> 410 al abrir el enlace", client.get(f"/api/v1/driver-invitations/{tok2}").status_code == 410)
check("revocar dos veces -> 400", client.post(f"/api/v1/transport/driver-invitations/{inv2['id']}/revoke", headers=admin).status_code == 400)
r = client.post("/api/v1/transport/driver-invitations", json={"email": E2}, headers=admin)
inv3 = r.json(); tok3 = inv3["invite_url"].split("token=")[1]
with Session(engine) as db:
    db.execute(text("update driver_invitations set expires_at = now() - interval '1 hour' where id=:i"), {"i": inv3["id"]})
    db.commit()
check("vencida -> 410", client.get(f"/api/v1/driver-invitations/{tok3}").status_code == 410)
check("vencida aparece como 'expired' en la lista", next(x for x in client.get("/api/v1/transport/driver-invitations", headers=admin).json() if x["id"] == inv3["id"])["status"] == "expired")
check("aceptar una vencida -> 410", client.post(f"/api/v1/driver-invitations/{tok3}/accept", json=acc()).status_code == 410)

print("\n-- Límite de intentos --")
main._REGISTER_ATTEMPTS.clear()
codes = [client.get("/api/v1/driver-invitations/zzzzzzzz").status_code for _ in range(62)]
check("tras 60 consultas responde 429", codes[:60] == [404] * 60 and codes[60:] == [429, 429], str(set(codes)))
main._REGISTER_ATTEMPTS.clear()

print("\n-- Auditoría sin hashes --")
with Session(engine) as db:
    leaks = db.execute(text("select count(*) from audit_logs where created_at > now() - interval '15 minutes' and (changes::text like '%$2b$%' or changes::text like :t)"), {"t": f"%{tok1b}%"}).scalar()
    h = db.execute(text("select count(*) from audit_logs where table_name='driver_invitations' and created_at > now() - interval '15 minutes' and changes::text like '%[oculto]%'")).scalar()
check("0 hashes de contraseña ni tokens en audit_logs", leaks == 0, str(leaks))
check("token_hash aparece como [oculto]", h >= 1, str(h))

print("\nTODO OK\n--- Limpieza ---")
with Session(engine) as db:
    uid = db.execute(text("select id from users where email=:e"), {"e": E1}).scalar()
    did = db.execute(text("select id from drivers where user_id=:u"), {"u": uid}).scalar()
    inv_ids = [str(r[0]) for r in db.execute(text("select id from driver_invitations where email in (:a,:b)"), {"a": E1, "b": E2}).all()]
    db.execute(text("update route_headers set status=cast(:s as routestatus), vehicle_id=:v, driver_id=:d where id=:r"),
               {"s": orig[0], "v": orig[1], "d": orig[2], "r": cand["id"]})
    db.execute(text("delete from driver_invitations where email in (:a,:b)"), {"a": E1, "b": E2})
    db.execute(text("delete from notifications where entity_id = :d or user_id = :u"), {"d": str(did), "u": uid})
    db.execute(text("delete from certifications where driver_id=:d"), {"d": did})
    db.execute(text("delete from drivers where id=:d"), {"d": did})
    db.execute(text("delete from audit_logs where record_id = any(cast(:ids as text[])) or changed_by_user_id=:u"),
               {"ids": [str(uid), str(did), cand["id"]] + inv_ids, "u": uid})
    db.execute(text("delete from users where id=:u"), {"u": uid})
    db.commit()
    left = db.execute(text("select (select count(*) from users where email like 'test.drv.%'), (select count(*) from driver_invitations where email like 'test.drv.%')")).one()
print("   datos de prueba eliminados; restos:", tuple(left))

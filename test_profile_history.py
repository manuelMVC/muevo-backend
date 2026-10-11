# -*- coding: utf-8 -*-
"""
test_profile_history.py — Prueba de punta a punta del historial de cambios de perfil
(tabla profile_change_history, migración 033).

Llama a los endpoints REALES con TestClient y los datos reales de la base local,
pero dentro de UNA transacción externa que se revierte al final: no queda ningún
cambio en la base (ni perfiles de prueba, ni filas de historial, ni de auditoría).

Uso:
    cd C:\\claude\\muevo-backend
    .venv\\Scripts\\python test_profile_history.py

Requiere la migración 033 aplicada y los usuarios de demo (admin@, carlos@, operator@, viewer@).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

import main

FALLAS = []


def check(nombre, condicion, detalle=""):
    print(("  OK     " if condicion else "  FALLA  ") + nombre + ((" — " + str(detalle)) if (detalle and not condicion) else ""))
    if not condicion:
        FALLAS.append(nombre)


conn = main.engine.connect()
outer = conn.begin()


def override_get_db():
    s = Session(bind=conn, join_transaction_mode="create_savepoint", autoflush=False)
    try:
        yield s
    finally:
        s.close()


main.app.dependency_overrides[main.get_db] = override_get_db
client = TestClient(main.app)


def q(sql, **params):
    return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def user(email):
    r = q("select id::text id, email from users where email = :e", e=email)
    assert r, f"No existe el usuario de demo {email}"
    return r[0]


def hdr(email):
    u = user(email)
    return {"Authorization": "Bearer " + main.create_token(u["id"], u["email"])}


def hist(profile_code=None, start_seq=0):
    sql = "select * from profile_change_history where seq > :s"
    params = {"s": start_seq}
    if profile_code:
        sql += " and profile_code = :p"
        params["p"] = profile_code
    return q(sql + " order by seq", **params)


def max_seq():
    return q("select coalesce(max(seq), 0) m from profile_change_history")[0]["m"]


try:
    print("\n== Preparación ==")
    check("la tabla profile_change_history existe (migración 033)",
          bool(q("select 1 from information_schema.tables where table_name = 'profile_change_history'")))
    admin, carlos, viewer, operador = (user(e) for e in
                                       ("admin@muevo.app", "carlos@muevo.app", "viewer@muevo.app", "operator@muevo.app"))
    perms = {r["code"]: r["id"] for r in q("select code, id::text id from permissions where code in ('batches.view','batches.edit','clients.view')")}
    empresa = q("select id::text id, name from companies limit 1")[0]
    hu_viewer = q("select id::text id from holding_users where user_id = cast(:u as uuid)", u=viewer["id"])[0]["id"]
    hu_operador = q("select id::text id from holding_users where user_id = cast(:u as uuid)", u=operador["id"])[0]["id"]
    perfil_operator = q("select id::text id from profiles where code = 'operator'")[0]["id"]
    audit_delete_antes = q("select count(*) c from audit_logs where table_name = 'profile_permissions' and action = 'delete'")[0]["c"]
    base = max_seq()

    print("\n== 1. Crear un perfil con 2 permisos (admin de Muevo) ==")
    r = client.post("/api/v1/admin/profiles", headers=hdr("admin@muevo.app"),
                    json={"code": "test_hist", "name": "Perfil de prueba", "description": "temporal",
                          "permission_ids": [perms["batches.view"], perms["batches.edit"]]})
    check("POST /admin/profiles responde 201", r.status_code == 201, r.text[:200])
    h = hist("test_hist", base)
    tipos = sorted(x["change_type"] for x in h)
    check("queda 1 profile_created y 2 permission_added", tipos == ["permission_added", "permission_added", "profile_created"], tipos)
    check("la persona es admin@muevo.app y el origen es 'api'", all(x["changed_by_email"] == "admin@muevo.app" and x["source"] == "api" for x in h))
    check("la descripción nombra el permiso y el perfil",
          any("'batches.view'" in x["description"] and "'test_hist'" in x["description"] for x in h))
    pid = q("select id::text id from profiles where code = 'test_hist'")[0]["id"]

    print("\n== 2. Editar el perfil: renombrar y cambiar un permiso (batches.edit -> clients.view) ==")
    seq2 = max_seq()
    r = client.patch(f"/api/v1/admin/profiles/{pid}", headers=hdr("admin@muevo.app"),
                     json={"name": "Perfil renombrado", "permission_ids": [perms["batches.view"], perms["clients.view"]]})
    check("PATCH /admin/profiles responde 200", r.status_code == 200, r.text[:200])
    h = hist("test_hist", seq2)
    por_tipo = {x["change_type"]: x for x in h}
    check("solo 3 cambios: nombre, permiso quitado y permiso agregado (el permiso sin cambios NO genera fila)", len(h) == 3, [x["description"] for x in h])
    upd = por_tipo.get("profile_updated")
    check("el cambio de nombre guarda valor anterior y valor actual",
          bool(upd) and upd["field_name"] == "name" and upd["old_value"] == "Perfil de prueba" and upd["new_value"] == "Perfil renombrado", upd)
    check("permission_removed guarda el permiso quitado como valor anterior",
          bool(por_tipo.get("permission_removed")) and por_tipo["permission_removed"]["old_value"] == "batches.edit" and por_tipo["permission_removed"]["new_value"] is None)
    check("permission_added guarda el permiso nuevo como valor actual",
          bool(por_tipo.get("permission_added")) and por_tipo["permission_added"]["new_value"] == "clients.view")
    audit_delete_despues = q("select count(*) c from audit_logs where table_name = 'profile_permissions' and action = 'delete'")[0]["c"]
    check("el permiso quitado ahora también queda en audit_logs (ya no es un DELETE masivo)", audit_delete_despues == audit_delete_antes + 1,
          f"antes={audit_delete_antes} después={audit_delete_despues}")

    print("\n== 3. Asignar el perfil a viewer@ con alcance de una empresa ==")
    seq3 = max_seq()
    r = client.post("/api/v1/admin/users/profiles", headers=hdr("admin@muevo.app"),
                    json={"holding_user_id": hu_viewer, "profile_id": pid, "company_id": empresa["id"]})
    check("POST /admin/users/profiles responde 201", r.status_code == 201, r.text[:200])
    asig_id = r.json().get("id")
    h = hist("test_hist", seq3)
    check("queda profile_assigned con la persona afectada y el alcance",
          len(h) == 1 and h[0]["change_type"] == "profile_assigned" and h[0]["target_user_email"] == "viewer@muevo.app"
          and empresa["name"] in h[0]["new_value"], h)

    print("\n== 4. Cambiar la asignación: alcance a todo el holding y luego el perfil ==")
    seq4 = max_seq()
    r = client.patch(f"/api/v1/admin/users/profiles/{asig_id}", headers=hdr("admin@muevo.app"), json={"company_id": None})
    check("PATCH de la asignación (alcance) responde 200", r.status_code == 200, r.text[:200])
    r = client.patch(f"/api/v1/admin/users/profiles/{asig_id}", headers=hdr("admin@muevo.app"), json={"profile_id": perfil_operator})
    check("PATCH de la asignación (perfil) responde 200", r.status_code == 200, r.text[:200])
    h = hist(None, seq4)
    cambios = {x["field_name"]: x for x in h if x["change_type"] == "profile_assignment_changed"}
    check("el alcance guarda empresa -> 'todo el holding'",
          "company_id" in cambios and cambios["company_id"]["old_value"] == empresa["name"] and cambios["company_id"]["new_value"] == "todo el holding", cambios.get("company_id"))
    check("el perfil guarda test_hist -> operator",
          "profile_id" in cambios and cambios["profile_id"]["old_value"] == "test_hist" and cambios["profile_id"]["new_value"] == "operator", cambios.get("profile_id"))

    print("\n== 5. Quitar la asignación ==")
    seq5 = max_seq()
    r = client.delete(f"/api/v1/admin/users/profiles/{asig_id}", headers=hdr("admin@muevo.app"))
    check("DELETE de la asignación responde 204", r.status_code == 204, r.text[:200])
    h = hist(None, seq5)
    check("queda profile_unassigned con el valor anterior", len(h) == 1 and h[0]["change_type"] == "profile_unassigned" and "operator" in (h[0]["old_value"] or ""), h)

    print("\n== 6. Usuario del holding (carlos@, super admin): rol y accesos a empresas de operator@ ==")
    flags = q("select can_view, can_operate, can_invoice, is_admin from holding_user_companies where holding_user_id = cast(:h as uuid) and company_id = cast(:c as uuid)",
              h=hu_operador, c=empresa["id"])[0]
    acceso = {"company_id": empresa["id"], "can_view": flags["can_view"], "can_operate": not flags["can_operate"],
              "can_invoice": flags["can_invoice"], "is_admin": flags["is_admin"]}
    seq6 = max_seq()
    r = client.patch(f"/api/v1/holding/users/{hu_operador}", headers=hdr("carlos@muevo.app"), json={"role": "supervisor", "company_access": [acceso]})
    check("PATCH /holding/users responde 200", r.status_code == 200, r.text[:200])
    h = hist(None, seq6)
    tipos = sorted(x["change_type"] for x in h)
    check("solo 2 cambios: el rol y la bandera 'operar' (el acceso no cambiado NO se borra ni se vuelve a crear)",
          tipos == ["company_access_changed", "holding_user_updated"], tipos)
    ca = next((x for x in h if x["change_type"] == "company_access_changed"), {})
    check("la bandera guarda anterior y actual (sí/no)", ca.get("field_name") == "can_operate"
          and ca.get("old_value") == ("sí" if flags["can_operate"] else "no") and ca.get("new_value") == ("no" if flags["can_operate"] else "sí"), ca)
    check("la persona es carlos@muevo.app", all(x["changed_by_email"] == "carlos@muevo.app" for x in h))
    seq6b = max_seq()
    client.patch(f"/api/v1/holding/users/{hu_operador}", headers=hdr("carlos@muevo.app"), json={"company_access": []})
    h = hist(None, seq6b)
    check("quitar todos los accesos deja company_access_revoked (antes con DELETE masivo no quedaba rastro)",
          [x["change_type"] for x in h] == ["company_access_revoked"], [x["change_type"] for x in h])
    audit_huc = q("select count(*) c from audit_logs where table_name = 'holding_user_companies' and action = 'delete'")[0]["c"]
    check("y también queda en audit_logs", audit_huc >= 1)
    seq6c = max_seq()
    client.patch(f"/api/v1/holding/users/{hu_operador}", headers=hdr("carlos@muevo.app"), json={"company_access": [acceso]})
    h = hist(None, seq6c)
    check("volver a darlo deja company_access_granted", [x["change_type"] for x in h] == ["company_access_granted"], [x["change_type"] for x in h])

    print("\n== 7. Consulta del historial (GET /admin/profile-history) ==")
    r = client.get("/api/v1/admin/profile-history?profile_code=test_hist", headers=hdr("admin@muevo.app"))
    check("el admin de Muevo puede consultarlo (200)", r.status_code == 200, r.text[:200])
    filas = r.json() if r.status_code == 200 else []
    esperadas = len(hist("test_hist", base))
    check(f"devuelve las {esperadas} filas del perfil de prueba, de la más reciente a la más antigua",
          len(filas) == esperadas and [f["seq"] for f in filas] == sorted((f["seq"] for f in filas), reverse=True), len(filas))
    check("cada fila trae persona, fecha, cambio, valor anterior y valor actual",
          all(k in filas[0] for k in ("changed_by", "changed_at", "description", "old_value", "new_value")) if filas else False)
    r = client.get("/api/v1/admin/profile-history?target_user_email=viewer@muevo.app", headers=hdr("admin@muevo.app"))
    check("filtra por el usuario afectado", r.status_code == 200 and len(r.json()) == 4 and all(f["target_user"]["email"] == "viewer@muevo.app" for f in r.json()), r.text[:200])
    r = client.get("/api/v1/admin/profile-history", headers=hdr("operator@muevo.app"))
    check("un operator NO puede consultarlo (403)", r.status_code == 403, r.status_code)

    print("\n== 8. El historial es de solo lectura (trigger) ==")
    for sql, nombre in (("update profile_change_history set description = 'x'", "UPDATE"),
                        ("delete from profile_change_history", "DELETE"),
                        ("truncate profile_change_history", "TRUNCATE")):
        sp = conn.begin_nested()
        bloqueado = False
        try:
            conn.execute(text(sql))
        except Exception as e:
            bloqueado = "solo lectura" in str(e)
        sp.rollback()
        check(f"{nombre} sobre el historial es rechazado", bloqueado)

    print("\n== 9. El historial no se audita a sí mismo ==")
    check("audit_logs no tiene filas de profile_change_history",
          q("select count(*) c from audit_logs where table_name = 'profile_change_history'")[0]["c"] == 0)

finally:
    outer.rollback()
    conn.close()

print("\n== Limpieza ==")
with main.engine.connect() as c2:
    quedo_perfil = c2.execute(text("select count(*) from profiles where code = 'test_hist'")).scalar()
    quedo_hist = c2.execute(text("select count(*) from profile_change_history")).scalar()
check("la transacción se revirtió: no quedó el perfil de prueba", quedo_perfil == 0, quedo_perfil)
check("la transacción se revirtió: el historial real quedó como estaba (0 filas)", quedo_hist == 0, quedo_hist)

print("\n" + ("TODO OK" if not FALLAS else f"{len(FALLAS)} PRUEBA(S) FALLARON: " + "; ".join(FALLAS)))
sys.exit(1 if FALLAS else 0)

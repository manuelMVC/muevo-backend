"""
seed_geography.py — Carga la geografía base: EE.UU., sus 50 estados y las
ciudades de los 50 estados (data/us_geography.json), y completa
ciudad/estado de las paradas y warehouses que todavía no la tienen.

Las ciudades salen de los lugares postales de GeoNames (CC BY 4.0), con sus
códigos postales para resolver la ciudad de una parada por zip. Otras
ciudades se dan de alta solas cuando aparece una parada nueva (resolve_city
con create=True).

Idempotente: se puede correr las veces que haga falta.

Uso:
    cd C:\\Claude\\muevo-backend
    .venv\\Scripts\\python seed_geography.py
"""

import sys, os, json, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Country, State, City, RouteDetail, Warehouse
from main import engine, normalize_place_name, resolve_city

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "us_geography.json")


def main():
    data = json.load(open(DATA_FILE, encoding="utf-8"))

    with Session(engine) as db:
        c = data["country"]
        country = db.execute(select(Country).where(Country.iso2 == c["iso2"])).scalar_one_or_none()
        if not country:
            country = Country(id=uuid.uuid4(), iso2=c["iso2"], iso3=c["iso3"], name=c["name"])
            db.add(country)
            db.flush()
            print(f"+ País: {country.name}")

        state_by_code = {s.code: s for s in db.execute(select(State).where(State.country_id == country.id)).scalars()}
        new_states = 0
        for s in data["states"]:
            if s["code"] in state_by_code:
                continue
            st = State(id=uuid.uuid4(), country_id=country.id, code=s["code"], name=s["name"])
            db.add(st)
            state_by_code[s["code"]] = st
            new_states += 1
        db.flush()
        print(f"+ {new_states} estados nuevos ({len(state_by_code)} en total)")

        existing = {(ci.state_id, ci.name_normalized) for ci in db.execute(select(City)).scalars()}
        new_cities = 0
        for ci in data["cities"]:
            st = state_by_code.get(ci["state"])
            norm = normalize_place_name(ci["name"])
            if not st or (st.id, norm) in existing:
                continue
            db.add(City(id=uuid.uuid4(), state_id=st.id, name=ci["name"], name_normalized=norm,
                        lat=ci["lat"], lng=ci["lng"], zip_codes=ci["zip_codes"]))
            existing.add((st.id, norm))
            new_cities += 1
        db.commit()
        print(f"+ {new_cities} ciudades nuevas ({len(existing)} en total)")

        # ── Completar paradas existentes sin ciudad ──────────────────────────
        filled = skipped = 0
        for rd in db.execute(select(RouteDetail).where(RouteDetail.city_id.is_(None))).scalars():
            city = resolve_city(db, zip_code=rd.zip_code, city=rd.city, state=rd.state, lat=rd.lat, lng=rd.lng)
            if city:
                rd.city_id, rd.city, rd.state = city.id, city.name, city.state.code
                filled += 1
            else:
                skipped += 1
        # ── Warehouses por nombre de ciudad + estado ─────────────────────────
        wh_filled = 0
        for wh in db.execute(select(Warehouse).where(Warehouse.city_id.is_(None))).scalars():
            city = resolve_city(db, zip_code=wh.zip_code, city=wh.city, state=wh.state)
            if city:
                wh.city_id = city.id
                wh_filled += 1
        db.commit()
        print(f"+ {filled} paradas con ciudad asignada ({skipped} fuera de las zonas cargadas)")
        print(f"+ {wh_filled} warehouses con ciudad asignada")


if __name__ == "__main__":
    main()

"""
fix_batch_numbers.py — Asigna batch_number secuencial a lotes que no lo tienen.

Numera por compañía en el orden de creación (created_at).

Uso:
    cd C:\\muevo-backend
    .venv\\Scripts\\python fix_batch_numbers.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:Semeolvido.01@localhost:5432/muevodb")

from models import RouteBatch, Company

engine = create_engine(DATABASE_URL, echo=False)

with Session(engine) as db:
    companies = db.execute(select(Company)).scalars().all()
    total_fixed = 0

    for company in companies:
        # Lotes de esta compañía sin número, ordenados por fecha de creación
        batches = db.execute(
            select(RouteBatch)
            .where(RouteBatch.company_id == company.id, RouteBatch.batch_number == None)
            .order_by(RouteBatch.created_at.asc())
        ).scalars().all()

        if not batches:
            continue

        # Buscar el último número usado por esta compañía
        last = db.execute(
            select(RouteBatch.batch_number)
            .where(RouteBatch.company_id == company.id, RouteBatch.batch_number.isnot(None))
            .order_by(RouteBatch.batch_number.desc())
            .limit(1)
        ).scalar_one_or_none()

        n = int(last.split('-')[-1]) + 1 if last else 1

        print(f"\n🏢 {company.name} — {len(batches)} lote(s) sin numerar")
        for b in batches:
            b.batch_number = f"ORL-LOT-{n:04d}"
            print(f"  ✓ {(b.notes or str(b.id)[:8]):40} → {b.batch_number}")
            n += 1
            total_fixed += 1

    db.commit()
    print(f"\n✅ {total_fixed} lote(s) numerados en total.")

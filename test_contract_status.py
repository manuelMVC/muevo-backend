import psycopg2

conn = psycopg2.connect(host='localhost', dbname='muevodb', user='postgres', password='Semeolvido.01')
conn.autocommit = True
cur = conn.cursor()

print("=== Estructura de la tabla contracts ===")
cur.execute("""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'contracts' AND column_name IN ('status', 'is_active', 'renewed_into_id')
    ORDER BY column_name
""")
for row in cur.fetchall():
    print(row)

print("\n=== Contratos existentes (si hay alguno) ===")
cur.execute("SELECT contract_number, status, is_active FROM contracts LIMIT 10")
rows = cur.fetchall()
if not rows:
    print("(no hay contratos todavía — es esperado si no se creó ninguno)")
for row in rows:
    print(row)

print("\n=== Test del trigger: crear contrato draft y cambiar a active ===")
cur.execute("SELECT id FROM companies LIMIT 1")
company_row = cur.fetchone()
if company_row:
    company_id = company_row[0]
    cur.execute("""
        INSERT INTO contracts (id, company_id, contract_number, service_mode, base_rate, start_date, status)
        VALUES (gen_random_uuid(), %s, 'TEST-001', 'mensajeria', 50.00, NOW(), 'draft')
        RETURNING id, status, is_active
    """, (company_id,))
    new_id, status, is_active = cur.fetchone()
    print(f"Creado: status={status}, is_active={is_active} (esperado: draft, False)")

    cur.execute("UPDATE contracts SET status = 'active' WHERE id = %s RETURNING status, is_active", (new_id,))
    status, is_active = cur.fetchone()
    print(f"Tras UPDATE a active: status={status}, is_active={is_active} (esperado: active, True)")

    cur.execute("DELETE FROM contracts WHERE id = %s", (new_id,))
    print("Contrato de prueba eliminado.")
else:
    print("No hay companies en la base para probar — corre /dev/seed-routes primero.")

conn.close()

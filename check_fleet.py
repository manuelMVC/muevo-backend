import psycopg2

conn = psycopg2.connect(host='localhost', dbname='muevodb', user='postgres', password='Semeolvido.01')
cur = conn.cursor()

cur.execute("""
    SELECT v.id, v.plate, v.is_active, v.driver_id, v.transport_company_id
    FROM vehicles v
    JOIN transport_companies tc ON v.transport_company_id = tc.id
    WHERE tc.name = 'Rapid Courier Orlando LLC'
""")
rows = cur.fetchall()
print(f"Vehículos encontrados: {len(rows)}")
for r in rows:
    print(r)

conn.close()

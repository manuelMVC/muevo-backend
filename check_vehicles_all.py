import psycopg2

conn = psycopg2.connect(host='localhost', dbname='muevodb', user='postgres', password='Semeolvido.01')
cur = conn.cursor()

cur.execute("SELECT id, plate, driver_id, transport_company_id, is_active FROM vehicles")
rows = cur.fetchall()
print(f"Total de vehículos en la base: {len(rows)}")
for r in rows:
    print(r)

conn.close()

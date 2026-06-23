import psycopg2

conn = psycopg2.connect(host='localhost', dbname='muevodb', user='postgres', password='Semeolvido.01')
cur = conn.cursor()

print("=== USERS (driver) ===")
cur.execute("SELECT id, email FROM users WHERE email = 'driver@muevo.app'")
user = cur.fetchone()
print(user)
user_id = user[0] if user else None

print("\n=== DRIVERS ===")
cur.execute("SELECT id, user_id FROM drivers WHERE user_id = %s", (user_id,))
driver = cur.fetchone()
print(driver)
driver_id = driver[0] if driver else None

print("\n=== VEHICLES (for this driver) ===")
cur.execute("SELECT id, driver_id, transport_company_id, is_active FROM vehicles WHERE driver_id = %s", (driver_id,))
print(cur.fetchall())

print("\n=== ALL VEHICLES ===")
cur.execute("SELECT id, driver_id, transport_company_id, is_active, plate FROM vehicles")
print(cur.fetchall())

print("\n=== TRANSPORT COMPANIES ===")
cur.execute("SELECT id, name FROM transport_companies")
print(cur.fetchall())

print("\n=== ROUTE HEADERS ===")
cur.execute("SELECT id, route_number, transport_company_id, vehicle_id, dispatched_by, status FROM route_headers")
print(cur.fetchall())

conn.close()

import psycopg2
import uuid

conn = psycopg2.connect(host='localhost', dbname='muevodb', user='postgres', password='Semeolvido.01')
conn.autocommit = True
cur = conn.cursor()

# Get driver id
cur.execute("""
    SELECT d.id FROM drivers d
    JOIN users u ON u.id = d.user_id
    WHERE u.email = 'driver@muevo.app'
""")
driver_id = cur.fetchone()[0]
print("Driver ID:", driver_id)

# Get transport company id
cur.execute("SELECT id FROM transport_companies WHERE name = 'Rapid Courier Orlando LLC'")
tc_id = cur.fetchone()[0]
print("Transport Company ID:", tc_id)

# Create vehicle linking both
vehicle_id = str(uuid.uuid4())
cur.execute("""
    INSERT INTO vehicles (
        id, driver_id, transport_company_id, is_active,
        vehicle_type, make, model, year, color, plate
    ) VALUES (
        %s, %s, %s, TRUE,
        'furgoneta', 'Ford', 'Transit', 2021, 'Blanco', 'FL-RCO-001'
    )
""", (vehicle_id, driver_id, tc_id))

print("Vehicle created:", vehicle_id)

# Link this vehicle to the existing route_headers that don't have a vehicle yet
cur.execute("""
    UPDATE route_headers
    SET vehicle_id = %s
    WHERE transport_company_id = %s AND vehicle_id IS NULL
""", (vehicle_id, tc_id))
print("Routes updated:", cur.rowcount)

conn.close()
print("Done.")

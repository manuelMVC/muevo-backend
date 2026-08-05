import psycopg2
import uuid

conn = psycopg2.connect(host='localhost', dbname='muevodb', user='postgres', password='Semeolvido.01')
conn.autocommit = True
cur = conn.cursor()

# Empresa de transporte
cur.execute("SELECT id FROM transport_companies WHERE name = 'Rapid Courier Orlando LLC'")
row = cur.fetchone()
if not row:
    print("ERROR: La empresa de transporte no existe. Corre /dev/seed-routes primero.")
    exit()
tc_id = row[0]
print("Transport Company ID:", tc_id)

# Conductor existente (driver@muevo.app) para vincular al primer vehículo
cur.execute("""
    SELECT d.id FROM drivers d
    JOIN users u ON d.user_id = u.id
    WHERE u.email = 'driver@muevo.app'
""")
row = cur.fetchone()
driver_id = row[0] if row else None
print("Driver ID (driver@muevo.app):", driver_id)

vehicles = [
    dict(plate='FL-RCO-001', make='Ford', model='Transit', year=2022, color='Blanco',
         vehicle_type='furgoneta', payload_kg=900, volume_m3=11.5, driver_id=driver_id),
    dict(plate='FL-RCO-002', make='Mercedes-Benz', model='Sprinter', year=2023, color='Plateado',
         vehicle_type='furgoneta', payload_kg=1200, volume_m3=14.0, driver_id=None),
    dict(plate='FL-RCO-003', make='Isuzu', model='NPR', year=2021, color='Blanco',
         vehicle_type='furgon', payload_kg=2200, volume_m3=20.0, driver_id=None),
]

created = 0
for v in vehicles:
    cur.execute("SELECT id FROM vehicles WHERE plate = %s", (v['plate'],))
    if cur.fetchone():
        print(f"  {v['plate']} ya existe, omitiendo.")
        continue

    vehicle_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO vehicles (
            id, driver_id, transport_company_id, is_active,
            vehicle_type, make, model, year, color, plate,
            payload_kg, volume_m3, passenger_seats, fuel_type, odometer_miles
        ) VALUES (
            %s, %s, %s, TRUE,
            %s, %s, %s, %s, %s, %s,
            %s, %s, 2, 'gasoline', 0
        )
    """, (
        vehicle_id, v['driver_id'], tc_id,
        v['vehicle_type'], v['make'], v['model'], v['year'], v['color'], v['plate'],
        v['payload_kg'], v['volume_m3'],
    ))
    print(f"  Creado: {v['plate']} ({v['make']} {v['model']}) — ID {vehicle_id}")
    created += 1

print(f"\n{created} vehículo(s) creado(s) para Rapid Courier Orlando LLC.")
conn.close()

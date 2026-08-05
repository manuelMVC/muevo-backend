import psycopg2
import uuid

conn = psycopg2.connect(host='localhost', dbname='muevodb', user='postgres', password='Semeolvido.01')
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT id FROM users WHERE email = 'transport@muevo.app'")
row = cur.fetchone()
if not row:
    print("ERROR: transport@muevo.app no existe. Corre /dev/seed primero.")
    exit()
transport_user_id = row[0]
print("Transport user ID:", transport_user_id)

cur.execute("SELECT id, name FROM transport_companies WHERE name = 'Rapid Courier Orlando LLC'")
row = cur.fetchone()
if not row:
    print("ERROR: La empresa de transporte no existe. Corre /dev/seed-routes primero.")
    exit()
tc_id, tc_name = row
print("Transport Company:", tc_name, tc_id)

cur.execute("SELECT id FROM transport_company_admins WHERE user_id = %s AND transport_company_id = %s", (transport_user_id, tc_id))
if cur.fetchone():
    print("transport@muevo.app ya está vinculado como admin de esta empresa.")
else:
    admin_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO transport_company_admins (id, transport_company_id, user_id, is_primary, can_accept_routes, can_manage_fleet, can_view_billing)
        VALUES (%s, %s, %s, TRUE, TRUE, TRUE, TRUE)
    """, (admin_id, tc_id, transport_user_id))
    print("Vinculado correctamente. Admin ID:", admin_id)

conn.close()

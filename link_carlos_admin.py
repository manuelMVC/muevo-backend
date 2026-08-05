import psycopg2
import uuid

conn = psycopg2.connect(host='localhost', dbname='muevodb', user='postgres', password='Semeolvido.01')
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT id FROM users WHERE email = 'carlos@muevo.app'")
row = cur.fetchone()
if not row:
    print("ERROR: carlos@muevo.app no existe. Corre /dev/seed primero.")
    exit()
carlos_id = row[0]
print("Carlos user ID:", carlos_id)

cur.execute("SELECT id, name FROM companies WHERE name = 'LegalDocs Express LLC'")
row = cur.fetchone()
if not row:
    print("ERROR: La company no existe. Corre /dev/seed-routes primero.")
    exit()
company_id, company_name = row
print("Company:", company_name, company_id)

cur.execute("SELECT id FROM company_admins WHERE user_id = %s AND company_id = %s", (carlos_id, company_id))
if cur.fetchone():
    print("Carlos ya está vinculado como admin de esta company.")
else:
    admin_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO company_admins (id, company_id, user_id, is_primary, can_dispatch, can_invoice)
        VALUES (%s, %s, %s, TRUE, TRUE, TRUE)
    """, (admin_id, company_id, carlos_id))
    print("Vinculado correctamente. Admin ID:", admin_id)

conn.close()

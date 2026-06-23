import psycopg2
import bcrypt

conn = psycopg2.connect(host='localhost', dbname='muevodb', user='postgres', password='Semeolvido.01')
conn.autocommit = True
cur = conn.cursor()

new_hash = bcrypt.hashpw('muevo123'[:72].encode(), bcrypt.gensalt()).decode()

cur.execute("""
    UPDATE users
    SET hashed_password = %s
    WHERE email IN ('carlos@muevo.app', 'driver@muevo.app', 'admin@muevo.app')
""", (new_hash,))

print(f"Updated {cur.rowcount} users with new bcrypt hash")
conn.close()

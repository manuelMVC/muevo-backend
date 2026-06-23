import psycopg2

conn = psycopg2.connect(host='localhost', dbname='muevodb', user='postgres', password='Semeolvido.01')
cur = conn.cursor()

print("=== Active queries / locks ===")
cur.execute("""
    SELECT pid, state, wait_event_type, wait_event, query, query_start
    FROM pg_stat_activity
    WHERE datname = 'muevodb' AND pid != pg_backend_pid()
    ORDER BY query_start
""")
rows = cur.fetchall()
if not rows:
    print("No other connections found.")
for r in rows:
    print(r)

print("\n=== Lock details ===")
cur.execute("""
    SELECT pid, locktype, relation::regclass, mode, granted
    FROM pg_locks
    WHERE relation IS NOT NULL
    ORDER BY pid
""")
for r in cur.fetchall():
    print(r)

conn.close()

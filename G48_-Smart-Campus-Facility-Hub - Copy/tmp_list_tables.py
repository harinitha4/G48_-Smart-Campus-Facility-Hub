import sqlite3
conn = sqlite3.connect('database.db')
cur = conn.execute("select name from sqlite_master where type='table' order by name")
print([r[0] for r in cur.fetchall()])


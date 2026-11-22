import os
import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'hyip', '.env'))

def get_db_connection():
    return pymysql.connect(
        host=os.getenv('DB_HOST', '127.0.0.1'),
        port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USERNAME', 'root'),
        password=os.getenv('DB_PASSWORD', ''),
        database=os.getenv('DB_DATABASE', 'hyip'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False
    )

def execute_query(query, params=None, fetch=True):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(query, params or ())
            if fetch:
                result = cursor.fetchall()
            else:
                result = cursor.rowcount
            # Always commit — required because autocommit=False.
            # Selects are no-ops on commit; writes (UPDATE/INSERT/DELETE)
            # would be silently rolled back without this call
            # (this was the bug: early returns skipped the commit).
            conn.commit()
            if fetch:
                return result
            return cursor.lastrowid
    finally:
        conn.close()

def execute_many(query, params_list):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.executemany(query, params_list)
            conn.commit()
            return cursor.rowcount
    finally:
        conn.close()

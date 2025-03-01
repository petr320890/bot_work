import sqlite3

class Database:
    def __init__(self, db_path="database.sqlite"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()

    def execute(self, query, params=(), commit=False, fetchone=False, fetchall=False):
        self.cursor.execute(query, params)
        if commit:
            self.conn.commit()
        if fetchone:
            return self.cursor.fetchone()
        if fetchall:
            return self.cursor.fetchall()
        return None

    def close(self):
        self.conn.close()
"""Small DB-API compatibility layer for SQLite and PostgreSQL."""
import datetime
import os
import sqlite3

import psycopg
from psycopg.rows import tuple_row

DB_ERRORS = (sqlite3.Error, psycopg.Error)


class Record(dict):
    """Mapping with sqlite.Row-style positional indexing."""
    def __init__(self, names, values):
        self._values = tuple(
            value.isoformat(sep=' ') if isinstance(value, datetime.datetime)
            else value.isoformat() if isinstance(value, datetime.date)
            else value for value in values
        )
        super().__init__(zip(names, self._values))

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, (int, slice)) else super().__getitem__(key)


def record_factory(cursor):
    names = [col.name for col in cursor.description] if cursor.description else []
    return lambda values: Record(names, values)


class PostgresConnection:
    def __init__(self, url):
        self.connection = psycopg.connect(url, connect_timeout=15, row_factory=record_factory)

    def execute(self, sql, params=None):
        # Application statements use qmark placeholders, never literal question marks.
        if params is not None:
            sql = sql.replace('%', '%%').replace('?', '%s')
            return self.connection.execute(sql, params)
        return self.connection.execute(sql)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


def is_postgres(db):
    return isinstance(db, PostgresConnection)


def connect(sqlite_path, *, url=None):
    url = os.environ.get('DATABASE_URL', '') if url is None else url
    if url:
        if not url.startswith(('postgresql://', 'postgres://')):
            raise ValueError('DATABASE_URL must be a PostgreSQL connection URI')
        return PostgresConnection(url)
    os.makedirs(os.path.dirname(os.path.abspath(sqlite_path)), exist_ok=True)
    db = sqlite3.connect(sqlite_path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db


def begin_write(db, table):
    """Serialize legacy read-modify-write transactions on selected tables."""
    if table not in ('users', 'exam_attempts'):
        raise ValueError('Unsupported lock target')
    if is_postgres(db):
        db.execute(f'LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE')
    else:
        db.execute('BEGIN IMMEDIATE')

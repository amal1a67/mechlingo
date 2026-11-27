"""Offline SQLite -> empty PostgreSQL migration. Stop writes first."""
import argparse
import os
from pathlib import Path
import sqlite3

from database import _initialize
from db_backend import connect, is_postgres

# Foreign key order; include all currently defined application tables.
TABLES = (
    'users', 'user_stats', 'courses', 'course_sections', 'course_topics',
    'course_levels', 'level_questions', 'ticket_sets', 'oral_tickets',
    'ticket_ratings', 'exam_attempts', 'exam_attempt_questions', 'exam_mistakes',
    'user_courses', 'user_progress', 'daily_activity', 'achievements', 'user_achievements',
)
IDENTITIES = set(TABLES) - {'user_stats', 'ticket_ratings', 'exam_attempt_questions', 'exam_mistakes'}


def migrate(source, url):
    path = Path(source).resolve(strict=True)
    src = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    src.row_factory = sqlite3.Row
    target = connect(str(path), url=url)
    try:
        if not is_postgres(target):
            raise ValueError('A PostgreSQL DATABASE_URL is required')
        src.execute('BEGIN')
        if src.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity check failed')
        if src.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('SQLite contains broken foreign keys')
        present = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if present != set(TABLES):
            raise ValueError('Unexpected SQLite schema; use a current MechLingo backup')
        _initialize(target, seed=False)
        for table in TABLES:
            if target.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:
                raise ValueError('Target must be empty; refusing to overwrite data')
        counts = {}
        for table in TABLES:
            columns = [r[1] for r in src.execute(f'PRAGMA table_info({table})')]
            allowed = {r[0] for r in target.execute(
                'SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?', [table]).fetchall()}
            if not set(columns).issubset(allowed):
                raise ValueError(f'Unexpected columns in {table}')
            names = ','.join('"' + c.replace('"', '""') + '"' for c in columns)
            placeholders = ','.join('?' for _ in columns)
            cursor = src.execute(f'SELECT {names} FROM {table}')
            count = 0
            for row in cursor:
                target.execute(f'INSERT INTO {table} ({names}) VALUES ({placeholders})', tuple(row))
                count += 1
            if target.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] != count:
                raise ValueError(f'Row count mismatch in {table}')
            counts[table] = count
        for table in IDENTITIES:
            target.execute(f"""SELECT setval(pg_get_serial_sequence('{table}','id'),
                COALESCE((SELECT MAX(id) FROM {table}),1),
                EXISTS (SELECT 1 FROM {table}))""")
        target.commit()
        return counts
    except Exception:
        target.rollback()
        raise
    finally:
        src.close()
        target.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='Path to a consistent SQLite backup')
    args = parser.parse_args()
    url = os.environ.get('DATABASE_URL', '')
    if not url:
        parser.error('Set DATABASE_URL in the environment first')
    try:
        counts = migrate(args.source, url)
    except Exception as exc:
        # Do not log DSNs or database row contents (personal information).
        raise SystemExit(f'Migration failed ({type(exc).__name__}). No rows committed.') from None
    print('Migration committed. Row counts:')
    for table, count in counts.items():
        print(f'{table}: {count}')

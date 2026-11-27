"""Smoke test for a DISPOSABLE database; never run against production."""
import os
import tempfile
import unittest
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

# Redirect the SQLite test to a temporary directory before importing the app.
import database

TEMP = tempfile.TemporaryDirectory()
database.DATABASE = os.path.join(TEMP.name, 'smoke.sqlite')
if os.environ.get('DATABASE_URL') and os.environ.get('MECHLINGO_TEST_DB') != '1':
    raise RuntimeError('Use only a disposable PostgreSQL database')
from app import app
from database import get_db, init_db


class SmokeTest(unittest.TestCase):
    def test_end_to_end(self):
        app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        init_db()  # second initialization must not duplicate seed content
        with app.app_context():
            db = get_db()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM courses').fetchone()[0], 5)
            level = db.execute('SELECT id FROM course_levels ORDER BY id LIMIT 1').fetchone()[0]
            course = db.execute('SELECT id FROM courses ORDER BY id LIMIT 1').fetchone()[0]
            section = db.execute('SELECT id FROM course_sections ORDER BY id LIMIT 1').fetchone()[0]
            topic = db.execute('SELECT id FROM course_topics ORDER BY id LIMIT 1').fetchone()[0]
            uid = db.execute('INSERT INTO users(username,email,password,role,pro_until) VALUES (?,?,?,?,?) RETURNING id',
                ['test_admin','test@example.invalid',generate_password_hash('test-only-password'), 'admin',
                 (date.today()+timedelta(days=7)).isoformat()]).fetchone()[0]
            db.execute('INSERT INTO user_stats(user_id,has_onboarded) VALUES (?,1)', [uid])
            db.execute('INSERT INTO user_courses(user_id,course_id) VALUES (?,?)',[uid,course])
            db.commit()
        c = app.test_client()
        self.assertIn(c.post('/login', data={'username':'test_admin','password':'test-only-password'}).status_code,(302,303))
        for path in ['/dashboard','/courses','/reviews','/leaderboard','/profile',
                     f'/course/{course}',f'/section/{section}',f'/topic/{topic}',f'/level/{level}',
                     '/exam-center','/tickets','/admin','/admin/users','/admin/questions']:
            self.assertEqual(c.get(path).status_code,200,path)
        started=c.post('/api/exam/start').get_json()
        self.assertTrue(started.get('questions'), started)
        body={'token':started['token'],'answers':[None]*len(started['questions'])}
        finish=c.post('/api/exam/finish',json=body)
        self.assertEqual(finish.status_code,200)
        self.assertEqual(finish.get_json()['correct'],0)
        self.assertEqual(c.post('/api/exam/finish',json=body).get_json(),finish.get_json())
        self.assertEqual(c.get('/exam-history').status_code,200)


if __name__ == '__main__':
    unittest.main()

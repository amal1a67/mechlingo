import sqlite3
from flask import g
import os

DATABASE = os.path.join(os.path.dirname(__file__), 'instance', 'mechlingo.db')


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
    return db


def init_db():
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    db = sqlite3.connect(DATABASE)
    db.execute('PRAGMA foreign_keys = ON')

    # ==================== ПОЛЬЗОВАТЕЛИ ====================
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            avatar TEXT DEFAULT '',
            role TEXT DEFAULT 'student',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            streak INTEGER DEFAULT 0,
            longest_streak INTEGER DEFAULT 0,
            last_activity_date TEXT,
            hearts INTEGER DEFAULT 5,
            hearts_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            gems INTEGER DEFAULT 0,
            total_lessons INTEGER DEFAULT 0,
            total_correct INTEGER DEFAULT 0,
            total_answers INTEGER DEFAULT 0,
            has_onboarded INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')

    # ==================== КУРСЫ ====================
    db.execute('''
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            icon TEXT DEFAULT '📚',
            color TEXT DEFAULT '#7c3aed',
            order_index INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS course_sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            icon TEXT DEFAULT '📖',
            order_index INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS course_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            icon TEXT DEFAULT '📝',
            order_index INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (section_id) REFERENCES course_sections (id) ON DELETE CASCADE
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS course_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            level_type TEXT DEFAULT 'theory',
            order_index INTEGER DEFAULT 0,
            xp_reward INTEGER DEFAULT 10,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (topic_id) REFERENCES course_topics (id) ON DELETE CASCADE
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS level_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level_id INTEGER NOT NULL,
            question_type TEXT DEFAULT 'test',
            question TEXT NOT NULL,
            options_json TEXT DEFAULT '[]',
            correct_answer TEXT NOT NULL,
            explanation TEXT,
            hint TEXT,
            order_index INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (level_id) REFERENCES course_levels (id) ON DELETE CASCADE
        )
    ''')

    # ==================== ПРОГРЕСС ====================
    db.execute('''
        CREATE TABLE IF NOT EXISTS user_courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            is_active INTEGER DEFAULT 1,
            UNIQUE(user_id, course_id),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS user_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            level_id INTEGER NOT NULL,
            status TEXT DEFAULT 'locked',
            stars INTEGER DEFAULT 0,
            attempts INTEGER DEFAULT 0,
            best_score INTEGER DEFAULT 0,
            completed_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, level_id),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (level_id) REFERENCES course_levels (id) ON DELETE CASCADE
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS daily_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            activity_date TEXT NOT NULL,
            lessons_completed INTEGER DEFAULT 0,
            xp_earned INTEGER DEFAULT 0,
            UNIQUE(user_id, activity_date),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT,
            icon TEXT DEFAULT '🏆'
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS user_achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            achievement_id INTEGER NOT NULL,
            earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, achievement_id),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (achievement_id) REFERENCES achievements (id) ON DELETE CASCADE
        )
    ''')

    # ==================== МИГРАЦИИ ====================
    try:
        db.execute('ALTER TABLE user_stats ADD COLUMN has_onboarded INTEGER DEFAULT 0')
    except:
        pass

    # ==================== СТАРТОВЫЕ ДАННЫЕ ====================
    # Достижения
    default_achievements = [
        ('first_step', '🎯 Первый шаг', 'Пройди первый уровень', '🎯'),
        ('streak_3', '🔥 3 дня подряд', '3 дня подряд занимайся', '🔥'),
        ('streak_7', '⚡ Неделя огня', '7 дней подряд', '⚡'),
        ('streak_30', '🏆 Месяц силы', '30 дней подряд', '🏆'),
        ('level_5', '📚 Студент', 'Достигни 5 уровня', '📚'),
        ('level_10', '🎓 Отличник', 'Достигни 10 уровня', '🎓'),
        ('perfect', '⭐ Идеально!', 'Пройди уровень на 3 звезды', '⭐'),
        ('perfect_10', '💎 Перфекционист', '10 уровней на 3 звезды', '💎'),
        ('course_complete', '👑 Мастер', 'Пройди курс полностью', '👑'),
    ]
    for code, title, desc, icon in default_achievements:
        try:
            db.execute('INSERT INTO achievements (code, title, description, icon) VALUES (?,?,?,?)',
                       [code, title, desc, icon])
        except:
            pass

    # Стартовые курсы (только если их нет)
    existing = db.execute('SELECT COUNT(*) as c FROM courses').fetchone()[0]
    if existing == 0:
        default_courses = [
            ('Анатомия', 'Строение человеческого тела', '🦴', '#7c3aed', 1),
            ('Физиология', 'Функции органов и систем', '💓', '#ef4444', 2),
            ('Фармакология', 'Лекарства и их действие', '💊', '#22c55e', 3),
            ('Гистология', 'Микроскопическое строение тканей', '🔬', '#f59e0b', 4),
            ('Биохимия', 'Химические процессы в организме', '⚗️', '#06b6d4', 5),
        ]
        for name, desc, icon, color, order_idx in default_courses:
            db.execute('INSERT INTO courses (name, description, icon, color, order_index) VALUES (?,?,?,?,?)',
                       [name, desc, icon, color, order_idx])

    db.commit()
    db.close()


def close_db(e=None):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

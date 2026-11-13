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
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')

    # ==================== СОЗДАНИЕ ТАБЛИЦ ====================
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            avatar TEXT DEFAULT '',
            role TEXT DEFAULT 'student',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        );

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
        );

        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            icon TEXT DEFAULT '📚',
            color TEXT DEFAULT '#7c3aed',
            order_index INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS course_sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            icon TEXT DEFAULT '📖',
            order_index INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS course_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            icon TEXT DEFAULT '📝',
            order_index INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (section_id) REFERENCES course_sections (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS course_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            level_type TEXT DEFAULT 'theory',
            order_index INTEGER DEFAULT 0,
            xp_reward INTEGER DEFAULT 10,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (topic_id) REFERENCES course_topics (id) ON DELETE CASCADE
        );

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
        );

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
        );

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
        );

        CREATE TABLE IF NOT EXISTS daily_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            activity_date TEXT NOT NULL,
            lessons_completed INTEGER DEFAULT 0,
            xp_earned INTEGER DEFAULT 0,
            UNIQUE(user_id, activity_date),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT,
            icon TEXT DEFAULT '🏆'
        );

        CREATE TABLE IF NOT EXISTS user_achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            achievement_id INTEGER NOT NULL,
            earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, achievement_id),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (achievement_id) REFERENCES achievements (id) ON DELETE CASCADE
        );
    ''')

    # ==================== МИГРАЦИИ ====================
    def add_column_if_missing(table, column, ddl):
        cols = [r[1] for r in db.execute(f'PRAGMA table_info({table})').fetchall()]
        if column not in cols:
            db.execute(f'ALTER TABLE {table} ADD COLUMN {ddl}')

    add_column_if_missing('user_stats', 'daily_goal', 'daily_goal INTEGER DEFAULT 50')
    add_column_if_missing('daily_activity', 'goal_reached', 'goal_reached INTEGER DEFAULT 0')
    add_column_if_missing('daily_activity', 'goal_celebrated', 'goal_celebrated INTEGER DEFAULT 0')
    
    # ДЛЯ ЛИДЕРБОРДА
    add_column_if_missing('user_stats', 'weekly_xp', 'weekly_xp INTEGER DEFAULT 0')
    add_column_if_missing('user_stats', 'last_week_reset', 'last_week_reset TEXT DEFAULT ""')
    
    # ДЛЯ СИСТЕМЫ ПОВТОРЕНИЯ (SRS)
    add_column_if_missing('user_progress', 'next_review_date', 'next_review_date TEXT DEFAULT ""')

    db.execute('UPDATE user_stats SET daily_goal=50 WHERE daily_goal IS NULL')
    db.commit()

    # ==================== СТАРТОВЫЕ ДАННЫЕ (ДОСТИЖЕНИЯ) ====================
    default_achievements = [
        ('first_step', 'Первый шаг', 'Завершите свой первый уровень', '🩺'),
        ('streak_3', 'Стабильность', 'Занимайтесь 3 дня подряд', '📅'),
        ('streak_7', 'Дисциплина', 'Занимайтесь 7 дней подряд', '🗓️'),
        ('streak_30', 'Профессионализм', 'Занимайтесь 30 дней подряд', '🏅'),
        ('level_5', 'Ординатор', 'Достигните 5 уровня', '🎓'),
        ('level_10', 'Специалист', 'Достигните 10 уровня', '🧑‍⚕️'),
        ('perfect', 'Точность', 'Пройдите уровень на 3 звезды', '🎯'),
        ('perfect_10', 'Ювелирная работа', 'Получите 10 идеальных уровней', '💎'),
        ('course_complete', 'Полный курс', 'Завершите обучение по курсу', '📜'),
    ]
    for code, title, desc, icon in default_achievements:
        try:
            db.execute('INSERT INTO achievements (code, title, description, icon) VALUES (?,?,?,?)',
                       [code, title, desc, icon])
        except:
            pass

    # ==================== СТАРТОВЫЕ ДАННЫЕ (КУРСЫ) ====================
    existing_courses = db.execute('SELECT COUNT(*) as c FROM courses').fetchone()[0]
    if existing_courses == 0:
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

    # ==================== АВТО-ЗАПОЛНЕНИЕ ТЕСТОВЫМИ УРОВНЯМИ ====================
    has_sections = db.execute('SELECT COUNT(*) as c FROM course_sections').fetchone()[0]
    if has_sections == 0:
        course = db.execute('SELECT id FROM courses ORDER BY order_index LIMIT 1').fetchone()
        if course:
            cid = course['id']
            
            db.execute('INSERT INTO course_sections (course_id, name, description, icon) VALUES (?,?,?,?)',
                       [cid, 'Опорно-двигательная система', 'Кости, мышцы и суставы', '🦴'])
            section_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            
            db.execute('INSERT INTO course_topics (section_id, name, description, icon) VALUES (?,?,?,?)',
                       [section_id, 'Скелет человека', 'Основные кости и их функции', '🦴'])
            topic_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            
            for i in range(1, 6):
                db.execute('INSERT INTO course_levels (topic_id, name, xp_reward) VALUES (?,?,?)',
                           [topic_id, f'Уровень {i}', 10])
            
            lvl_res = db.execute('SELECT id FROM course_levels WHERE topic_id=? ORDER BY order_index LIMIT 1', [topic_id]).fetchone()
            if lvl_res:
                lvl_id = lvl_res['id']
                q_options = '["Ребро", "Позвонок", "Лопатка", "Ключица"]'
                db.execute('INSERT INTO level_questions (level_id, question, options_json, correct_answer) VALUES (?,?,?,?)',
                           [lvl_id, 'Какая кость изображена на картинке?', q_options, 'Позвонок'])
                
                q_options2 = '["Бедренная", "Большая берцовая", "Локтевая", "Лучевая"]'
                db.execute('INSERT INTO level_questions (level_id, question, options_json, correct_answer) VALUES (?,?,?,?)',
                           [lvl_id, 'Какая кость самая длинная в теле человека?', q_options2, 'Бедренная'])

            print("✅ Тестовые разделы, темы и уровни добавлены в базу!")

    db.commit()
    db.close()


def close_db(e=None):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

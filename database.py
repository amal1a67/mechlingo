import sqlite3
from flask import g
import os
import json

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
    add_column_if_missing('user_stats', 'weekly_xp', 'weekly_xp INTEGER DEFAULT 0')
    add_column_if_missing('user_stats', 'last_week_reset', 'last_week_reset TEXT DEFAULT ""')
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

    # ==================== СТАРТОВЫЕ ДАННЫЕ (КУРСЫ И ВОПРОСЫ) ====================
    existing_courses = db.execute('SELECT COUNT(*) as c FROM courses').fetchone()[0]
    
    if existing_courses == 0:
        # 1. Создаем курсы
        courses_data = [
            ('Анатомия', 'Строение человеческого тела', '🦴', '#7c3aed', 1),
            ('Физиология', 'Функции органов и систем', '💓', '#ef4444', 2),
            ('Фармакология', 'Лекарства и их действие', '💊', '#22c55e', 3),
            ('Гистология', 'Микроскопическое строение тканей', '🔬', '#f59e0b', 4),
            ('Биохимия', 'Химические процессы в организме', '⚗️', '#06b6d4', 5),
        ]
        for name, desc, icon, color, order_idx in courses_data:
            db.execute('INSERT INTO courses (name, description, icon, color, order_index) VALUES (?,?,?,?,?)',
                       [name, desc, icon, color, order_idx])
        db.commit()

        # Словарь с вопросами для каждого курса
        questions_by_course = {
            "Анатомия": [
                ("Какая кость самая длинная в теле человека?", ["Бедренная", "Плечевая", "Локтевая", "Большая берцовая"], "Бедренная"),
                ("Сколько ребер у взрослого человека?", ["20", "24", "26", "12"], "24"),
                ("Какая кость не относится к черепу?", ["Лобная", "Затылочная", "Лопатка", "Теменная"], "Лопатка"),
                ("Какой отдел позвоночника содержит 5 позвонков?", ["Шейный", "Грудной", "Поясничный", "Крестцовый"], "Поясничный"),
                ("Какая мышца является самой сильной в теле?", ["Жевательная", "Икроножная", "Сердечная", "Дельтовидная"], "Жевательная"),
                ("Сколько камер в сердце человека?", ["2", "3", "4", "5"], "4"),
                ("Какая артерия снабжает кровью головной мозг?", ["Сонная", "Плечевая", "Бедренная", "Легочная"], "Сонная"),
                ("Где находится аппендикс?", ["Слепая кишка", "Желудок", "Печень", "Прямая кишка"], "Слепая кишка"),
                ("Какая кость образует плечевой сустав?", ["Плечевая", "Локтевая", "Лучевая", "Ключица"], "Плечевая"),
                ("Как называется самая крупная железа в организме?", ["Печень", "Поджелудочная", "Щитовидная", "Слюнная"], "Печень"),
                ("Сколько зубов у взрослого человека?", ["28", "30", "32", "36"], "32"),
                ("Какая структура соединяет кости в суставе?", ["Связки", "Сухожилия", "Фасции", "Мышцы"], "Связки"),
                ("Какой орган вырабатывает инсулин?", ["Печень", "Поджелудочная железа", "Почки", "Надпочечники"], "Поджелудочная железа"),
                ("Где находится гипофиз?", ["В брюшной полости", "В черепе", "В грудной клетке", "В тазу"], "В черепе"),
                ("Какая кость входит в состав таза?", ["Подвздошная", "Бедренная", "Крестец", "Седалищная"], "Подвздошная")
            ],
            "Физиология": [
                ("Какой орган отвечает за перекачивание крови?", ["Печень", "Сердце", "Легкие", "Почки"], "Сердце"),
                ("Где происходит газообмен в легких?", ["В бронхах", "В трахее", "В альвеолах", "В гортани"], "В альвеолах"),
                ("Какой гормон регулирует уровень глюкозы в крови?", ["Инсулин", "Адреналин", "Тироксин", "Кортизол"], "Инсулин"),
                ("Какой процесс обеспечивает движение крови по сосудам?", ["Сокращение сердца", "Дыхание", "Пищеварение", "Мочеобразование"], "Сокращение сердца"),
                ("Что такое гомеостаз?", ["Постоянство внутренней среды", "Рост организма", "Размножение", "Дыхание"], "Постоянство внутренней среды"),
                ("Какой газ выделяется при выдохе?", ["Кислород", "Углекислый газ", "Азот", "Водород"], "Углекислый газ"),
                ("Какой орган фильтрует кровь?", ["Печень", "Почки", "Селезенка", "Легкие"], "Почки"),
                ("Что происходит при сокращении диафрагмы?", ["Вдох", "Выдох", "Глотание", "Кашель"], "Вдох"),
                ("Какой витамин синтезируется в коже под действием солнца?", ["Витамин A", "Витамин C", "Витамин D", "Витамин B12"], "Витамин D"),
                ("Как называется процесс образования мочи?", ["Фильтрация", "Секреция", "Реабсорбция", "Диурез"], "Диурез"),
                ("Какой гормон вырабатывается в надпочечниках?", ["Инсулин", "Адреналин", "Тестостерон", "Эстроген"], "Адреналин"),
                ("Какой отдел мозга отвечает за равновесие?", ["Кора", "Мозжечок", "Гипоталамус", "Продолговатый мозг"], "Мозжечок"),
                ("Какой фермент расщепляет белки в желудке?", ["Амилаза", "Липаза", "Пепсин", "Трипсин"], "Пепсин"),
                ("Что такое эритроциты?", ["Красные кровяные тельца", "Белые кровяные тельца", "Тромбоциты", "Плазма"], "Красные кровяные тельца"),
                ("Какая мышца сокращается непроизвольно?", ["Скелетная", "Сердечная", "Гладкая", "Мимическая"], "Сердечная")
            ],
            "Фармакология": [
                ("Какой антибиотик является препаратом выбора при анафилактическом шоке?", ["Адреналин", "Преднизолон", "Димедрол", "Сульфакамфокаин"], "Адреналин"),
                ("Какой антидот используется при отравлении опиоидами?", ["Налоксон", "Атропин", "Ацетилцистеин", "Унитиол"], "Налоксон"),
                ("Какое лекарство снижает давление?", ["Аспирин", "Эналаприл", "Парацетамол", "Диклофенак"], "Эналаприл"),
                ("Какой препарат используется для разжижения крови?", ["Варфарин", "Аскорбиновая кислота", "Анальгин", "Но-шпа"], "Варфарин"),
                ("Что такое антибиотики?", ["Противовирусные", "Противобактериальные", "Противогрибковые", "Обезболивающие"], "Противобактериальные"),
                ("Какой препарат применяется при бронхиальной астме?", ["Сальбутамол", "Амоксициллин", "Омепразол", "Метформин"], "Сальбутамол"),
                ("Какой гормон используется при лечении диабета?", ["Инсулин", "Тироксин", "Кортизол", "Адреналин"], "Инсулин"),
                ("Какое лекарство от головной боли?", ["Парацетамол", "Амоксициллин", "Омепразол", "Варфарин"], "Парацетамол"),
                ("Что такое НПВС?", ["Нестероидные противовоспалительные средства", "Неорганические препараты", "Нервные стимуляторы", "Ноотропы"], "Нестероидные противовоспалительные средства"),
                ("Какой препарат применяется для наркоза?", ["Пропофол", "Аспирин", "Метформин", "Сальбутамол"], "Пропофол"),
                ("Какой витамин нужно принимать при беременности?", ["Фолиевая кислота", "Витамин C", "Витамин D", "Витамин A"], "Фолиевая кислота"),
                ("Какой препарат снижает уровень холестерина?", ["Статины", "Антибиотики", "Анальгетики", "Диуретики"], "Статины"),
                ("Какой диуретик выводит воду из организма?", ["Фуросемид", "Аспирин", "Парацетамол", "Омепразол"], "Фуросемид"),
                ("Какой препарат лечит аллергию?", ["Супрастин", "Амоксициллин", "Метформин", "Варфарин"], "Супрастин"),
                ("Что такое плацебо?", ["Пустышка", "Лекарство от всех болезней", "Витамин", "Антибиотик"], "Пустышка")
            ],
            "Гистология": [
                ("Какая ткань образует эпидермис кожи?", ["Эпителиальная", "Соединительная", "Мышечная", "Нервная"], "Эпителиальная"),
                ("Какая ткань содержит много межклеточного вещества?", ["Эпителиальная", "Соединительная", "Мышечная", "Нервная"], "Соединительная"),
                ("Какие клетки передают нервные импульсы?", ["Нейроны", "Эритроциты", "Лейкоциты", "Тромбоциты"], "Нейроны"),
                ("Какая ткань обеспечивает движение?", ["Эпителиальная", "Соединительная", "Мышечная", "Нервная"], "Мышечная"),
                ("Где находится однослойный эпителий?", ["Кожа", "Слизистые оболочки", "Кости", "Мышцы"], "Слизистые оболочки"),
                ("Какая ткань образует кровь?", ["Эпителиальная", "Соединительная", "Мышечная", "Нервная"], "Соединительная"),
                ("Как называются клетки хряща?", ["Хондроциты", "Остеоциты", "Миоциты", "Нейроциты"], "Хондроциты"),
                ("Как называются клетки кости?", ["Хондроциты", "Остеоциты", "Миоциты", "Нейроциты"], "Остеоциты"),
                ("Какая ткань образует связки?", ["Эпителиальная", "Соединительная", "Мышечная", "Нервная"], "Соединительная"),
                ("Какие клетки участвуют в свертывании крови?", ["Тромбоциты", "Эритроциты", "Лейкоциты", "Нейроны"], "Тромбоциты"),
                ("Какая ткань выстилает изнутри кишечник?", ["Эпителиальная", "Соединительная", "Мышечная", "Нервная"], "Эпителиальная"),
                ("Как называются клетки печени?", ["Гепатоциты", "Нефроциты", "Кардиомиоциты", "Пневмоциты"], "Гепатоциты"),
                ("Как называются клетки почек?", ["Гепатоциты", "Нефроциты", "Кардиомиоциты", "Пневмоциты"], "Нефроциты"),
                ("Как называются клетки сердечной мышцы?", ["Гепатоциты", "Нефроциты", "Кардиомиоциты", "Пневмоциты"], "Кардиомиоциты"),
                ("Какая ткань содержит жировые клетки?", ["Эпителиальная", "Соединительная", "Мышечная", "Нервная"], "Соединительная")
            ],
            "Биохимия": [
                ("Что является основным источником энергии в клетке?", ["АТФ", "ДНК", "РНК", "Белок"], "АТФ"),
                ("Какой процесс синтеза белка называется?", ["Транскрипция", "Трансляция", "Репликация", "Гликолиз"], "Трансляция"),
                ("Что такое ферменты?", ["Белки-катализаторы", "Углеводы", "Липиды", "Витамины"], "Белки-катализаторы"),
                ("Какой углевод является основным источником энергии?", ["Глюкоза", "Сахароза", "Крахмал", "Целлюлоза"], "Глюкоза"),
                ("Какой витамин участвует в свертывании крови?", ["Витамин K", "Витамин C", "Витамин A", "Витамин D"], "Витамин K"),
                ("Какой гормон регулирует уровень кальция в крови?", ["Паратгормон", "Инсулин", "Адреналин", "Тироксин"], "Паратгормон"),
                ("Что такое рН крови в норме?", ["7.35-7.45", "6.0-7.0", "7.5-8.0", "8.0-9.0"], "7.35-7.45"),
                ("Какой белок переносит кислород в крови?", ["Гемоглобин", "Альбумин", "Фибриноген", "Иммуноглобулин"], "Гемоглобин"),
                ("Какая кислота участвует в цикле Кребса?", ["Лимонная", "Уксусная", "Серная", "Соляная"], "Лимонная"),
                ("Что такое гликолиз?", ["Расщепление глюкозы", "Синтез белка", "Расщепление жиров", "Синтез ДНК"], "Расщепление глюкозы"),
                ("Какой витамин участвует в зрении?", ["Витамин A", "Витамин C", "Витамин D", "Витамин K"], "Витамин A"),
                ("Что такое ДНК?", ["Носитель генетической информации", "Белок", "Липид", "Углевод"], "Носитель генетической информации"),
                ("Какой процесс синтеза РНК называется?", ["Транскрипция", "Трансляция", "Репликация", "Гликолиз"], "Транскрипция"),
                ("Какой витамин участвует в синтезе коллагена?", ["Витамин C", "Витамин A", "Витамин D", "Витамин K"], "Витамин C"),
                ("Что такое окислительное фосфорилирование?", ["Синтез АТФ", "Синтез белка", "Расщепление глюкозы", "Синтез ДНК"], "Синтез АТФ")
            ]
        }

        # Проходимся по каждому курсу и создаем раздел, тему, уровни и вопросы
        for course_name, questions in questions_by_course.items():
            course = db.execute('SELECT id FROM courses WHERE name=?', [course_name]).fetchone()
            if not course:
                continue
            cid = course['id']

            # Создаем раздел
            db.execute('INSERT INTO course_sections (course_id, name, description, icon) VALUES (?,?,?,?)',
                       [cid, 'Базовый раздел', 'Основные понятия и практика', '📖'])
            section_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

            # Создаем тему
            db.execute('INSERT INTO course_topics (section_id, name, description, icon) VALUES (?,?,?,?)',
                       [section_id, 'Введение', 'Основы дисциплины', '📝'])
            topic_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

            # Создаем 5 уровней
            level_ids = []
            for i in range(1, 6):
                db.execute('INSERT INTO course_levels (topic_id, name, xp_reward) VALUES (?,?,?)',
                           [topic_id, f'Уровень {i}', 10])
                level_ids.append(db.execute('SELECT last_insert_rowid()').fetchone()[0])

            # Распределяем вопросы по уровням (по 3 вопроса на уровень)
            for i, (q_text, q_opts, q_correct) in enumerate(questions):
                lvl_id = level_ids[i % 5] # Раскидываем по кругу
                opts_json = json.dumps(q_opts, ensure_ascii=False)
                db.execute('INSERT INTO level_questions (level_id, question, options_json, correct_answer, explanation) VALUES (?,?,?,?,?)',
                           [lvl_id, q_text, opts_json, q_correct, "Базовое объяснение."])

        print(f"✅ Создано 5 курсов с вопросами!")

    db.commit()
    db.close()


def close_db(e=None):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

import sqlite3
import json
import os

DATABASE = os.path.join(os.path.dirname(__file__), 'instance', 'mechlingo.db')

def seed():
    if not os.path.exists(DATABASE):
        print("База не найдена. Сначала запусти app.py.")
        return

    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row

    # 1. Проверяем, есть ли уже разделы у Анатомии
    anatomy = db.execute("SELECT id FROM courses WHERE name='Анатомия'").fetchone()
    if not anatomy:
        print("Курс 'Анатомия' не найден.")
        return

    course_id = anatomy['id']
    sections_count = db.execute("SELECT COUNT(*) as c FROM course_sections WHERE course_id=?", [course_id]).fetchone()['c']
    
    if sections_count > 0:
        print("Контент уже добавлен. Выходим.")
        return

    print("Наполняем курс 'Анатомия'...")

    # ==================== РАЗДЕЛ 1: ОСТЕОЛОГИЯ ====================
    db.execute("INSERT INTO course_sections (course_id, name, description, order_index) VALUES (?,?,?,?)",
               [course_id, "Остеология", "Строение и соединение костей", 1])
    section_osteo = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Тема 1: Череп
    db.execute("INSERT INTO course_topics (section_id, name, description, order_index) VALUES (?,?,?,?)",
               [section_osteo, "Череп", "Кости мозгового и лицевого черепа", 1])
    topic_skull = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Уровни для темы "Череп"
    levels = [
        ("Введение в череп", "theory", 1, 15),
        ("Мозговой отдел", "theory", 2, 15),
        ("Лицевой отдел", "theory", 3, 15),
        ("Клинический случай: Травма", "clinical", 4, 25),
        ("Отработка: Названия костей", "practice", 5, 20)
    ]
    
    for lv_name, lv_type, order_idx, xp in levels:
        db.execute("INSERT INTO course_levels (topic_id, name, level_type, order_index, xp_reward) VALUES (?,?,?,?,?)",
                   [topic_skull, lv_name, lv_type, order_idx, xp])
        level_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Вопросы для этого уровня
        if lv_name == "Введение в череп":
            qs = [
                {"q": "Сколько костей в черепе взрослого человека?", "opts": ["22", "26", "29", "32"], "a": "B", "e": "Взрослый череп состоит из 26 костей."},
                {"q": "Какая кость не относится к мозговому отделу черепа?", "opts": ["Лобная", "Затылочная", "Верхняя челюсть", "Теменная"], "a": "C", "e": "Верхняя челюсть — это лицевой отдел."}
            ]
        elif lv_name == "Мозговой отдел":
            qs = [
                {"q": "Какая кость содержит большое затылочное отверстие?", "opts": ["Клиновидная", "Решетчатая", "Затылочная", "Височная"], "a": "C", "e": "Через большое затылочное отверстие проходит спинной мозг."},
                {"q": "Где находится турецкое седло?", "opts": ["В лобной кости", "В клиновидной кости", "В теменной кости", "В височной кости"], "a": "B", "e": "Турецкое седло — это углубление в теле клиновидной кости, где лежит гипофиз."}
            ]
        elif lv_name == "Лицевой отдел":
            qs = [
                {"q": "Какая кость формирует нижнюю челюсть?", "opts": ["Верхнечелюстная", "Скуловая", "Нижнечелюстная", "Небная"], "a": "C", "e": "Нижнечелюстная кость — единственная подвижная кость черепа."},
                {"q": "Какая кость участвует в образовании твердого неба?", "opts": ["Слезная", "Небная", "Носовая", "Сошник"], "a": "B", "e": "Небная кость и верхняя челюсть образуют твердое небо."}
            ]
        elif lv_name == "Клинический случай: Травма":
            qs = [
                {"q": "У пациента после удара по голове наблюдается ликворея (истечение спинномозговой жидкости) из носа. Перелом какой кости наиболее вероятен?", "opts": ["Лобной", "Решетчатой (пробита решетчатая пластинка)", "Скуловой", "Нижнечелюстной"], "a": "B", "e": "Решетчатая пластинка решетчатой кости имеет отверстия для обонятельных нитей, при её переломе возникает ликворея."}
            ]
        else:
            qs = [
                {"q": "Выберите все кости, относящиеся к лицевому отделу черепа:", "opts": ["Слезная", "Клиновидная", "Сошник", "Затылочная"], "a": "A", "e": "Слезная кость и сошник — лицевые. Клиновидная и затылочная — мозговые."}
            ]

        for i, q_data in enumerate(qs):
            db.execute("INSERT INTO level_questions (level_id, question_type, question, options_json, correct_answer, explanation, order_index) VALUES (?,?,?,?,?,?,?)",
                       [level_id, 'test', q_data['q'], json.dumps(q_data['opts']), q_data['a'], q_data['e'], i])

    # Тема 2: Позвоночник
    db.execute("INSERT INTO course_topics (section_id, name, description, order_index) VALUES (?,?,?,?)",
               [section_osteo, "Позвоночник", "Строение позвонков и отделы", 2])
    topic_spine = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Тема 3: Кости конечностей
    db.execute("INSERT INTO course_topics (section_id, name, description, order_index) VALUES (?,?,?,?)",
               [section_osteo, "Кости конечностей", "Скелет верхних и нижних конечностей", 3])
    
    # Тема 4: Таз
    db.execute("INSERT INTO course_topics (section_id, name, description, order_index) VALUES (?,?,?,?)",
               [section_osteo, "Таз", "Строение тазового пояса", 4])

    db.commit()
    db.close()
    print("✅ Контент успешно добавлен!")

if __name__ == '__main__':
    seed()

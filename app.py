from flask import Flask, render_template, request, redirect, url_for, jsonify, session, g
from database import get_db, init_db, close_db
import hashlib, os, datetime, json

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'mechlingo-secret-key-change-in-production')
app.teardown_appcontext(close_db)

with app.app_context():
    init_db()


# ==================== ХЕЛПЕРЫ ====================

def hash_password(p):
    return hashlib.sha256(p.encode()).hexdigest()


def get_user():
    if 'user_id' in session:
        user = get_db().execute('SELECT * FROM users WHERE id=?', [session['user_id']]).fetchone()
        if not user:
            session.pop('user_id', None)
            return None
        return user
    return None


def get_user_stats(user_id):
    db = get_db()
    stats = db.execute('SELECT * FROM user_stats WHERE user_id=?', [user_id]).fetchone()
    if not stats:
        user_exists = db.execute('SELECT id FROM users WHERE id=?', [user_id]).fetchone()
        if not user_exists:
            return None
        db.execute('INSERT INTO user_stats (user_id) VALUES (?)', [user_id])
        db.commit()
        stats = db.execute('SELECT * FROM user_stats WHERE user_id=?', [user_id]).fetchone()
    return stats


def login_required(f):
    def wrapper(*args, **kwargs):
        if not get_user():
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper


# === НОВЫЙ ДЕКОРАТОР ДЛЯ АДМИНА ===
def admin_required(f):
    def wrapper(*args, **kwargs):
        user = get_user()
        if not user or user['role'] != 'admin':
            return redirect(url_for('dashboard')) # Выкидываем обычных юзеров
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

# ... (Функции update_streak, check_achievements, get_daily_progress, check_level_unlocked 
# и остальные роуты регистрации/курсов оставляем БЕЗ ИЗМЕНЕНИЙ, они у вас уже есть) ...


# ==================== АДМИН-ПАНЕЛЬ ====================

@app.route('/admin')
@admin_required
def admin_dashboard():
    db = get_db()
    # Получаем все уровни, чтобы админ мог выбрать, куда добавить вопрос
    levels = db.execute('''
        SELECT cl.id, cl.name, ct.name as topic_name, c.name as course_name 
        FROM course_levels cl
        JOIN course_topics ct ON cl.topic_id = ct.id
        JOIN course_sections cs ON ct.section_id = cs.id
        JOIN courses c ON cs.course_id = c.id
        ORDER BY c.name, ct.name, cl.order_index
    ''').fetchall()
    
    # Получаем все вопросы (для отображения списка)
    questions = db.execute('''
        SELECT lq.*, cl.name as level_name 
        FROM level_questions lq
        JOIN course_levels cl ON lq.level_id = cl.id
        ORDER BY lq.id DESC
    ''').fetchall()
    
    return render_template('admin.html', user=get_user(), levels=levels, questions=questions, stats=get_user_stats(session['user_id']))


@app.route('/admin/add_question', methods=['POST'])
@admin_required
def admin_add_question():
    level_id = request.form.get('level_id')
    question_text = request.form.get('question')
    options = [
        request.form.get('opt1'),
        request.form.get('opt2'),
        request.form.get('opt3'),
        request.form.get('opt4')
    ]
    correct_answer = request.form.get('correct_answer')
    explanation = request.form.get('explanation', '')

    if not level_id or not question_text or not correct_answer:
        return "Ошибка: заполните все обязательные поля", 400

    # Очищаем пустые варианты
    options = [opt for opt in options if opt.strip()]

    db = get_db()
    db.execute('''
        INSERT INTO level_questions (level_id, question_type, question, options_json, correct_answer, explanation, order_index)
        VALUES (?, 'test', ?, ?, ?, ?, 99)
    ''', [level_id, question_text, json.dumps(options, ensure_ascii=False), correct_answer, explanation])
    db.commit()

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_question/<int:q_id>', methods=['POST'])
@admin_required
def admin_delete_question(q_id):
    db = get_db()
    db.execute('DELETE FROM level_questions WHERE id=?', [q_id])
    db.commit()
    return redirect(url_for('admin_dashboard'))

# ==================== ОСТАЛЬНЫЕ РОУТЫ ====================
# (Вставьте сюда все остальные ваши роуты: /dashboard, /course, /level, /profile и т.д., 
# они остаются точно такими же, как в предыдущем файле)
# ...

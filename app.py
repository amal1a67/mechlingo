from flask import Flask, render_template, request, redirect, url_for, jsonify, session, g
from database import get_db, init_db, close_db
import hashlib, os, datetime, json
import hmac
import secrets
from werkzeug.security import generate_password_hash, check_password_hash
import csv
import io

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=os.environ.get('RENDER') == 'true')
app.teardown_appcontext(close_db)

with app.app_context():
    init_db()


# ==================== ХЕЛПЕРЫ ====================

def hash_password(p):
    return generate_password_hash(p, method='scrypt')


def verify_password(stored, password):
    if len(stored) == 64 and all(c in '0123456789abcdef' for c in stored):
        return hmac.compare_digest(stored, hashlib.sha256(password.encode()).hexdigest()), True
    try:
        return check_password_hash(stored, password), False
    except (ValueError, TypeError):
        return False, False


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


def update_streak(user_id):
    db = get_db()
    stats = get_user_stats(user_id)
    if not stats:
        return 0
        
    today = datetime.date.today().isoformat()
    last = stats['last_activity_date']

    if last == today:
        return stats['streak']

    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    if last == yesterday:
        new_streak = stats['streak'] + 1
    else:
        new_streak = 1

    new_longest = max(stats['longest_streak'], new_streak)
    db.execute('''
        UPDATE user_stats
        SET streak=?, longest_streak=?, last_activity_date=?, updated_at=CURRENT_TIMESTAMP
        WHERE user_id=?
    ''', [new_streak, new_longest, today, user_id])
    db.commit()
    return new_streak


# ==================== ДОСТИЖЕНИЯ ====================

def check_achievements(user_id):
    db = get_db()
    stats = get_user_stats(user_id)
    if not stats:
        return []

    earned = [r['code'] for r in db.execute('''
        SELECT a.code FROM achievements a 
        JOIN user_achievements ua ON a.id = ua.achievement_id 
        WHERE ua.user_id=?
    ''', [user_id]).fetchall()]

    new_achievements = []

    def grant(code):
        ach = db.execute('SELECT id, title FROM achievements WHERE code=?', [code]).fetchone()
        if ach:
            try:
                db.execute('INSERT INTO user_achievements (user_id, achievement_id) VALUES (?,?)', [user_id, ach['id']])
                new_achievements.append(ach['title'])
            except:
                pass

    if 'first_step' not in earned and (stats['total_lessons'] or 0) >= 1:
        grant('first_step')

    streak = stats['streak'] or 0
    if 'streak_3' not in earned and streak >= 3: grant('streak_3')
    if 'streak_7' not in earned and streak >= 7: grant('streak_7')
    if 'streak_30' not in earned and streak >= 30: grant('streak_30')

    level = stats['level'] or 1
    if 'level_5' not in earned and level >= 5: grant('level_5')
    if 'level_10' not in earned and level >= 10: grant('level_10')

    perfect_res = db.execute("SELECT COUNT(*) as c FROM user_progress WHERE user_id=? AND stars=3", [user_id]).fetchone()
    perfect_count = perfect_res['c'] if perfect_res else 0
    if 'perfect' not in earned and perfect_count >= 1: grant('perfect')
    if 'perfect_10' not in earned and perfect_count >= 10: grant('perfect_10')

    if 'course_complete' not in earned:
        complete_course = db.execute('''
            SELECT c.id FROM courses c
            JOIN course_sections cs ON cs.course_id = c.id
            JOIN course_topics ct ON ct.section_id = cs.id
            JOIN course_levels cl ON cl.topic_id = ct.id
            LEFT JOIN user_progress up ON up.level_id = cl.id AND up.user_id = ? AND up.status = 'completed'
            GROUP BY c.id
            HAVING COUNT(cl.id) = COUNT(up.id) AND COUNT(cl.id) > 0
            LIMIT 1
        ''', [user_id]).fetchone()
        if complete_course:
            grant('course_complete')

    db.commit()
    return new_achievements


# ==================== ГЕЙМИФИКАЦИЯ ====================

def check_streak_reset(user_id):
    db = get_db()
    stats = get_user_stats(user_id)
    if not stats:
        return

    last = stats['last_activity_date']
    if not last:
        return

    try:
        last_date = datetime.date.fromisoformat(last)
    except (ValueError, TypeError):
        return

    today = datetime.date.today()
    if (today - last_date).days > 1 and (stats['streak'] or 0) > 0:
        db.execute(
            'UPDATE user_stats SET streak=0, updated_at=CURRENT_TIMESTAMP WHERE user_id=?',
            [user_id]
        )
        db.commit()


def check_weekly_reset(user_id):
    db = get_db()
    stats = get_user_stats(user_id)
    if not stats:
        return

    today = datetime.date.today()
    last_reset = stats['last_week_reset'] if 'last_week_reset' in stats.keys() else ''
    
    need_reset = False
    if not last_reset:
        need_reset = True
    else:
        try:
            last_date = datetime.date.fromisoformat(last_reset)
            if (today - last_date).days >= 7:
                need_reset = True
        except:
            need_reset = True

    if need_reset:
        db.execute('UPDATE user_stats SET weekly_xp=0, last_week_reset=? WHERE user_id=?', 
                   [today.isoformat(), user_id])
        db.commit()


def get_daily_progress(user_id):
    db = get_db()
    stats = get_user_stats(user_id)
    if not stats:
        return None

    today = datetime.date.today().isoformat()

    activity = db.execute(
        'SELECT * FROM daily_activity WHERE user_id=? AND activity_date=?',
        [user_id, today]
    ).fetchone()

    goal = stats['daily_goal'] or 50
    xp_today = activity['xp_earned'] if activity else 0
    goal_reached = bool(activity and activity['goal_reached'])

    return {
        'xp_today': xp_today,
        'goal': goal,
        'percent': min(100, int(xp_today / goal * 100)) if goal else 0,
        'goal_reached': goal_reached,
        'week': get_week_activity(user_id),
    }


def get_week_activity(user_id):
    db = get_db()
    today = datetime.date.today()
    start = today - datetime.timedelta(days=6)

    rows = db.execute(
        'SELECT * FROM daily_activity WHERE user_id=? AND activity_date >= ?',
        [user_id, start.isoformat()]
    ).fetchall()
    by_date = {r['activity_date']: r for r in rows}

    day_names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
    week = []
    for i in range(7):
        d = start + datetime.timedelta(days=i)
        iso = d.isoformat()
        act = by_date.get(iso)
        week.append({
            'date': iso,
            'day_name': day_names[d.weekday()],
            'xp': act['xp_earned'] if act else 0,
            'goal_reached': bool(act and act['goal_reached']),
            'is_today': d == today,
        })
    return week


def get_reviews_to_do(user_id):
    db = get_db()
    today = datetime.date.today().isoformat()
    
    reviews = db.execute('''
        SELECT up.level_id, cl.name as level_name, ct.name as topic_name, c.name as course_name
        FROM user_progress up
        JOIN course_levels cl ON up.level_id = cl.id
        JOIN course_topics ct ON cl.topic_id = ct.id
        JOIN course_sections cs ON ct.section_id = cs.id
        JOIN courses c ON cs.course_id = c.id
        WHERE up.user_id = ? 
          AND up.status = 'completed' 
          AND up.next_review_date <= ?
          AND up.next_review_date != ''
        ORDER BY up.next_review_date ASC
        LIMIT 20
    ''', [user_id, today]).fetchall()
    
    return reviews


@app.before_request
def _check_streak_on_request():
    if 'user_id' in session:
        check_streak_reset(session['user_id'])
        check_weekly_reset(session['user_id'])


# ==================== ПРОВЕРКА ДОСТУПА К УРОВНЮ ====================

def check_level_unlocked(user_id, level_id):
    db = get_db()
    level = db.execute('SELECT * FROM course_levels WHERE id=?', [level_id]).fetchone()
    if not level:
        return False

    first_level = db.execute('''
        SELECT id FROM course_levels
        WHERE topic_id=? ORDER BY order_index LIMIT 1
    ''', [level['topic_id']]).fetchone()

    if first_level and first_level['id'] == level_id:
        return True

    prev_level = db.execute('''
        SELECT id FROM course_levels
        WHERE topic_id=? AND order_index < ?
        ORDER BY order_index DESC LIMIT 1
    ''', [level['topic_id'], level['order_index']]).fetchone()

    if not prev_level:
        return True

    progress = db.execute('''
        SELECT status FROM user_progress
        WHERE user_id=? AND level_id=?
    ''', [user_id, prev_level['id']]).fetchone()

    return bool(progress and progress['status'] == 'completed')


def get_level_progress(user_id, level_id):
    db = get_db()
    return db.execute('''
        SELECT * FROM user_progress WHERE user_id=? AND level_id=?
    ''', [user_id, level_id]).fetchone()


# ==================== РЕГИСТРАЦИЯ / ВХОД ====================

@app.route('/')
def landing():
    user = get_user()
    if user:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if not username or not password or not email:
            return render_template('register.html', error='Заполните все поля')

        if len(username) < 3:
            return render_template('register.html', error='Имя пользователя минимум 3 символа')

        if len(password) < 6:
            return render_template('register.html', error='Пароль минимум 6 символов')

        db = get_db()
        if db.execute('SELECT id FROM users WHERE username=?', [username]).fetchone():
            return render_template('register.html', error='Пользователь уже существует')

        if db.execute('SELECT id FROM users WHERE email=?', [email]).fetchone():
            return render_template('register.html', error='Email уже зарегистрирован')

        db.execute('INSERT INTO users (username, email, password) VALUES (?,?,?)',
                   [username, email, hash_password(password)])
        db.commit()

        user_id = db.execute('SELECT id FROM users WHERE username=?', [username]).fetchone()['id']
        db.execute('INSERT INTO user_stats (user_id) VALUES (?)', [user_id])
        db.commit()

        session.clear()
        session['user_id'] = user_id
        return redirect(url_for('onboarding'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            return render_template('login.html', error='Заполните все поля')

        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username=?', [username]).fetchone()
        valid, legacy = verify_password(user['password'], password) if user else (False, False)

        if not valid:
            return render_template('login.html', error='Неверный логин или пароль')

        if legacy:
            db.execute('UPDATE users SET password=? WHERE id=? AND password=?',
                       [hash_password(password), user['id'], user['password']])
        session.clear()
        session['user_id'] = user['id']
        db.execute('UPDATE users SET last_login=CURRENT_TIMESTAMP WHERE id=?', [user['id']])
        db.commit()

        stats = get_user_stats(user['id'])
        if not stats or not stats['has_onboarded']:
            return redirect(url_for('onboarding'))

        return redirect(url_for('dashboard'))

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('landing'))


# ==================== ONBOARDING ====================

@app.route('/onboarding')
@login_required
def onboarding():
    user = get_user()
    db = get_db()
    stats = get_user_stats(user['id'])

    courses = db.execute('SELECT * FROM courses WHERE is_active=1 ORDER BY order_index').fetchall()
    chosen = [r['course_id'] for r in db.execute('SELECT course_id FROM user_courses WHERE user_id=?', [user['id']]).fetchall()]

    return render_template('onboarding.html',
                           user=user, stats=stats,
                           courses=courses, chosen=chosen)


@app.route('/api/onboarding/complete', methods=['POST'])
@login_required
def onboarding_complete():
    user = get_user()
    db = get_db()
    data = request.get_json()
    course_ids = data.get('course_ids', [])

    if not course_ids:
        return jsonify({'success': False, 'error': 'Выбери хотя бы один курс'}), 400

    db.execute('DELETE FROM user_courses WHERE user_id=?', [user['id']])
    for cid in course_ids:
        try:
            db.execute('INSERT INTO user_courses (user_id, course_id) VALUES (?,?)',
                       [user['id'], int(cid)])
        except:
            pass

    db.execute('UPDATE user_stats SET has_onboarded=1 WHERE user_id=?', [user['id']])
    db.commit()

    return jsonify({'success': True, 'redirect': '/dashboard'})


@app.route('/courses/change', methods=['GET', 'POST'])
@login_required
def change_courses():
    user = get_user()
    db = get_db()
    stats = get_user_stats(user['id'])

    if request.method == 'POST':
        course_ids = request.form.getlist('course_ids')
        db.execute('DELETE FROM user_courses WHERE user_id=?', [user['id']])
        for cid in course_ids:
            try:
                db.execute('INSERT INTO user_courses (user_id, course_id) VALUES (?,?)',
                           [user['id'], int(cid)])
            except:
                pass
        db.commit()
        return redirect(url_for('dashboard'))

    courses = db.execute('SELECT * FROM courses WHERE is_active=1 ORDER BY order_index').fetchall()
    chosen = [r['course_id'] for r in db.execute('SELECT course_id FROM user_courses WHERE user_id=?', [user['id']]).fetchall()]
    return render_template('change_courses.html', user=user, stats=stats, courses=courses, chosen=chosen)


# ==================== ДАШБОРД ====================

@app.route('/dashboard')
@login_required
def dashboard():
    user = get_user()
    db = get_db()
    stats = get_user_stats(user['id'])

    if not stats or not stats['has_onboarded']:
        return redirect(url_for('onboarding'))

    user_courses = db.execute('''
        SELECT c.*, uc.started_at,
               (SELECT COUNT(*) FROM course_sections WHERE course_id=c.id) as sections_count,
               (SELECT COUNT(*) FROM course_levels l JOIN course_topics t ON t.id=l.topic_id JOIN course_sections s ON s.id=t.section_id WHERE s.course_id=c.id) AS total_levels,
               (SELECT COUNT(DISTINCT l.id) FROM course_levels l JOIN course_topics t ON t.id=l.topic_id JOIN course_sections s ON s.id=t.section_id JOIN user_progress p ON p.level_id=l.id WHERE s.course_id=c.id AND p.user_id=uc.user_id AND p.status='completed') AS completed_levels
        FROM courses c
        JOIN user_courses uc ON uc.course_id = c.id
        WHERE uc.user_id = ?
        ORDER BY uc.started_at DESC
    ''', [user['id']]).fetchall()

    all_courses = db.execute('''
        SELECT c.* FROM courses c
        WHERE c.is_active = 1
        AND c.id NOT IN (SELECT course_id FROM user_courses WHERE user_id=?)
        ORDER BY c.order_index
    ''', [user['id']]).fetchall()

    daily = get_daily_progress(user['id'])
    reviews = get_reviews_to_do(user['id'])

    return render_template('dashboard.html',
                           user=user, stats=stats,
                           user_courses=user_courses,
                           all_courses=all_courses,
                           daily=daily,
                           reviews=reviews)


# ==================== КУРСЫ ====================

@app.route('/courses')
@login_required
def courses_page():
    user = get_user()
    db = get_db()
    stats = get_user_stats(user['id'])
    all_courses = db.execute('SELECT * FROM courses WHERE is_active=1 ORDER BY order_index').fetchall()
    
    try:
        user_courses = db.execute('SELECT course_id FROM user_courses WHERE user_id=?', [user['id']]).fetchall()
        user_course_ids = [r['course_id'] for r in user_courses]
    except Exception:
        user_course_ids = []
        
    return render_template('courses.html', user=user, stats=stats, courses=all_courses, user_course_ids=user_course_ids)


@app.route('/courses/<int:course_id>/add', methods=['POST'])
@login_required
def add_course(course_id):
    user = get_user()
    db = get_db()
    try:
        db.execute('INSERT INTO user_courses (user_id, course_id) VALUES (?,?)', [user['id'], course_id])
        db.commit()
    except:
        pass
    return redirect(url_for('dashboard'))


@app.route('/courses/<int:course_id>/remove', methods=['POST'])
@login_required
def remove_course(course_id):
    user = get_user()
    db = get_db()
    db.execute('DELETE FROM user_courses WHERE user_id=? AND course_id=?', [user['id'], course_id])
    db.commit()
    return redirect(url_for('dashboard'))


@app.route('/course/<int:course_id>')
@login_required
def course_view(course_id):
    user = get_user()
    db = get_db()
    stats = get_user_stats(user['id'])
    course = db.execute('SELECT * FROM courses WHERE id=?', [course_id]).fetchone()
    if not course:
        return redirect(url_for('dashboard'))

    sections = db.execute('SELECT * FROM course_sections WHERE course_id=? ORDER BY order_index', [course_id]).fetchall()

    sections_with_progress = []
    for s in sections:
        topics = db.execute('SELECT id FROM course_topics WHERE section_id=?', [s['id']]).fetchall()
        topic_ids = [t['id'] for t in topics]
        
        total_levels = 0
        completed_levels = 0
        
        if topic_ids:
            placeholders = ','.join(['?'] * len(topic_ids))
            total_res = db.execute(f'SELECT COUNT(*) as c FROM course_levels WHERE topic_id IN ({placeholders})', topic_ids).fetchone()
            total_levels = total_res['c'] if total_res else 0
            
            comp_res = db.execute(f'SELECT COUNT(*) as c FROM user_progress WHERE user_id=? AND status="completed" AND level_id IN (SELECT id FROM course_levels WHERE topic_id IN ({placeholders}))', [user['id']] + topic_ids).fetchone()
            completed_levels = comp_res['c'] if comp_res else 0

        percent = int(completed_levels / total_levels * 100) if total_levels > 0 else 0

        sections_with_progress.append({
            'section': s,
            'total': total_levels,
            'completed': completed_levels,
            'percent': percent
        })

    return render_template('course.html',
                           user=user, stats=stats, course=course,
                           sections=sections_with_progress)


# ==================== РАЗДЕЛЫ ====================

@app.route('/section/<int:section_id>')
@login_required
def section_view(section_id):
    user = get_user()
    db = get_db()
    stats = get_user_stats(user['id'])
    section = db.execute('SELECT * FROM course_sections WHERE id=?', [section_id]).fetchone()
    if not section:
        return redirect(url_for('dashboard'))

    course = db.execute('SELECT * FROM courses WHERE id=?', [section['course_id']]).fetchone()

    topics = db.execute('SELECT * FROM course_topics WHERE section_id=? ORDER BY order_index', [section_id]).fetchall()

    topics_with_progress = []
    for t in topics:
        total_res = db.execute('SELECT COUNT(*) as c FROM course_levels WHERE topic_id=?', [t['id']]).fetchone()
        total = total_res['c'] if total_res else 0

        completed_res = db.execute('SELECT COUNT(*) as c FROM user_progress WHERE user_id=? AND status="completed" AND level_id IN (SELECT id FROM course_levels WHERE topic_id=?)', [user['id'], t['id']]).fetchone()
        completed = completed_res['c'] if completed_res else 0

        percent = int(completed / total * 100) if total > 0 else 0

        topics_with_progress.append({
            'topic': t,
            'total': total,
            'completed': completed,
            'percent': percent
        })

    return render_template('section.html',
                           user=user, stats=stats, course=course, section=section,
                           topics=topics_with_progress)


# ==================== ТЕМЫ ====================

@app.route('/topic/<int:topic_id>')
@login_required
def topic_view(topic_id):
    user = get_user()
    db = get_db()
    stats = get_user_stats(user['id'])
    topic = db.execute('SELECT * FROM course_topics WHERE id=?', [topic_id]).fetchone()
    if not topic:
        return redirect(url_for('dashboard'))

    section = db.execute('SELECT * FROM course_sections WHERE id=?', [topic['section_id']]).fetchone()
    course = db.execute('SELECT * FROM courses WHERE id=?', [section['course_id']]).fetchone()

    levels = db.execute('SELECT * FROM course_levels WHERE topic_id=? ORDER BY order_index', [topic_id]).fetchall()

    levels_with_status = []
    for lv in levels:
        progress = get_level_progress(user['id'], lv['id'])
        unlocked = check_level_unlocked(user['id'], lv['id'])

        levels_with_status.append({
            'level': lv,
            'progress': progress,
            'unlocked': unlocked,
            'status': progress['status'] if progress else ('unlocked' if unlocked else 'locked'),
            'stars': progress['stars'] if progress else 0,
        })

    return render_template('topic.html',
                           user=user, stats=stats, course=course, section=section, topic=topic,
                           levels=levels_with_status)


# ==================== ПРОХОЖДЕНИЕ УРОВНЯ ====================

@app.route('/level/<int:level_id>')
@login_required
def level_view(level_id):
    user = get_user()
    db = get_db()
    stats = get_user_stats(user['id'])
    level = db.execute('SELECT * FROM course_levels WHERE id=?', [level_id]).fetchone()
    if not level:
        return redirect(url_for('dashboard'))

    if not check_level_unlocked(user['id'], level_id):
        return redirect(url_for('topic_view', topic_id=level['topic_id']))

    questions = db.execute('SELECT * FROM level_questions WHERE level_id=? ORDER BY order_index', [level_id]).fetchall()

    q_list = []
    for q in questions:
        try:
            options = json.loads(q['options_json']) if q['options_json'] else []
        except:
            options = []

        q_list.append({
            'id': q['id'],
            'question': q['question'] or "Вопрос",
            'options': options if options else ["Да", "Нет"],
            'correct_answer': q['correct_answer'] or "",
            'explanation': q['explanation'] or '',
            'question_type': q['question_type'] or 'test',
        })

    topic = db.execute('SELECT * FROM course_topics WHERE id=?', [level['topic_id']]).fetchone()
    if not topic:
        topic = {'id': 0, 'name': 'Неизвестно'}

    return render_template('level.html',
                           user=user, stats=stats, level=level, topic=topic, questions=q_list)


@app.route('/api/level/<int:level_id>/complete', methods=['POST'])
@login_required
def api_complete_level(level_id):
    user = get_user()
    db = get_db()
    data = request.get_json() or {}
    correct_count = int(data.get('correct', 0))
    total_count = int(data.get('total', 1))

    if total_count == 0:
        total_count = 1

    percent = int(correct_count / total_count * 100)

    if percent >= 95:
        stars = 3
    elif percent >= 80:
        stars = 2
    elif percent >= 60:
        stars = 1
    else:
        stars = 0

    level = db.execute('SELECT * FROM course_levels WHERE id=?', [level_id]).fetchone()
    xp_earned = int(level['xp_reward'] * (percent / 100)) if percent >= 60 else 0

    today_date = datetime.date.today()
    if stars == 3:
        next_review = (today_date + datetime.timedelta(days=7)).isoformat()
    elif stars == 2:
        next_review = (today_date + datetime.timedelta(days=3)).isoformat()
    elif stars == 1:
        next_review = (today_date + datetime.timedelta(days=1)).isoformat()
    else:
        next_review = today_date.isoformat()

    existing = db.execute('SELECT * FROM user_progress WHERE user_id=? AND level_id=?',
                          [user['id'], level_id]).fetchone()

    if existing:
        best_stars = max(existing['stars'], stars)
        best_score = max(existing['best_score'], percent)
        new_status = 'completed' if best_stars >= 1 else existing['status']
        
        new_next_review = next_review if stars >= existing['stars'] else existing['next_review_date']

        db.execute('''
            UPDATE user_progress
            SET stars=?, best_score=?, attempts=attempts+1, status=?, updated_at=CURRENT_TIMESTAMP,
                completed_at=CASE WHEN ?='completed' THEN CURRENT_TIMESTAMP ELSE completed_at END,
                next_review_date=?
            WHERE user_id=? AND level_id=?
        ''', [best_stars, best_score, new_status, new_status, new_next_review, user['id'], level_id])
    else:
        new_status = 'completed' if stars >= 1 else 'unlocked'
        db.execute('''
            INSERT INTO user_progress (user_id, level_id, status, stars, attempts, best_score, completed_at, next_review_date)
            VALUES (?,?,?,?,1,?, CASE WHEN ?='completed' THEN CURRENT_TIMESTAMP ELSE NULL END, ?)
        ''', [user['id'], level_id, new_status, stars, percent, new_status, next_review])

    stats = get_user_stats(user['id'])
    new_xp = stats['xp'] + xp_earned
    new_level = new_xp // 100 + 1
    new_gems = stats['gems'] + (1 if stars >= 2 else 0)

    db.execute('''
        UPDATE user_stats
        SET xp=?, level=?, gems=?, total_lessons=total_lessons+1, 
            weekly_xp=weekly_xp + ?, updated_at=CURRENT_TIMESTAMP
        WHERE user_id=?
    ''', [new_xp, new_level, new_gems, xp_earned, user['id']])

    today = datetime.date.today().isoformat()
    activity = db.execute(
        'SELECT * FROM daily_activity WHERE user_id=? AND activity_date=?',
        [user['id'], today]
    ).fetchone()

    goal = stats['daily_goal'] or 50
    goal_reached_now = False

    if activity:
        was_reached = bool(activity['goal_reached'])
        new_day_xp = activity['xp_earned'] + xp_earned
        db.execute('''
            UPDATE daily_activity
            SET lessons_completed = lessons_completed + 1,
                xp_earned = xp_earned + ?
            WHERE user_id=? AND activity_date=?
        ''', [xp_earned, user['id'], today])
    else:
        was_reached = False
        new_day_xp = xp_earned
        db.execute('''
            INSERT INTO daily_activity (user_id, activity_date, lessons_completed, xp_earned)
            VALUES (?,?,1,?)
        ''', [user['id'], today, xp_earned])

    if not was_reached and new_day_xp >= goal and xp_earned > 0:
        db.execute('''
            UPDATE daily_activity SET goal_reached=1
            WHERE user_id=? AND activity_date=?
        ''', [user['id'], today])
        goal_reached_now = True

    db.commit()

    streak_value = stats['streak']
    if xp_earned > 0:
        streak_value = update_streak(user['id'])

    new_achievements = check_achievements(user['id'])

    return jsonify({
        'success': True,
        'stars': stars,
        'xp_earned': xp_earned,
        'percent': percent,
        'correct': correct_count,
        'total': total_count,
        'total_xp': new_xp,
        'new_level': new_level,
        'level_up': new_level > stats['level'],
        'streak': streak_value,
        'goal_reached': goal_reached_now,
        'daily': get_daily_progress(user['id']),
        'new_achievements': new_achievements,
    })


# ==================== ЭКЗАМЕН ====================

def has_pro(user):
    if not user:
        return False
    try:
        return datetime.date.fromisoformat(user['pro_until']) >= datetime.date.today()
    except (ValueError, TypeError, KeyError, IndexError):
        return False


@app.cli.command('grant-pro')
def grant_pro():
    """Grant a test subscription via a trusted server console."""
    import click
    username = click.prompt('Username')
    days = click.prompt('Days', type=click.IntRange(1, 366))
    db = get_db()
    expiry = (datetime.date.today() + datetime.timedelta(days=days - 1)).isoformat()
    result = db.execute('UPDATE users SET pro_until=? WHERE username=?', [expiry, username])
    if not result.rowcount:
        raise click.ClickException('User not found')
    db.commit()
    click.echo('Access through ' + expiry)


@app.cli.command('import-tickets')
def import_tickets():
    """Import an author-reviewed JSON collection from a trusted console."""
    import click
    filename = click.prompt('JSON file', type=click.Path(exists=True, dir_okay=False))
    try:
        with open(filename, encoding='utf-8') as f:
            data = json.load(f)
        title, source, tickets = data['title'].strip(), data['source'].strip(), data['tickets']
        if not title or not source or not isinstance(tickets,list) or not tickets:
            raise ValueError('Title, source and tickets required')
        numbers = set()
        for ticket in tickets:
            number = ticket['number']
            if type(number) is not int or number < 1 or number in numbers:
                raise ValueError('Ticket numbers must be unique positive integers')
            numbers.add(number)
            questions = ticket['questions']
            if not isinstance(questions,list) or not questions:
                raise ValueError('Each ticket needs questions')
            for q in questions:
                if not all(isinstance(q.get(k),str) and q[k].strip() for k in ('question','answer')):
                    raise ValueError('Each question needs a question and reference answer')
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        raise click.ClickException(str(exc))
    db = get_db()
    if db.execute('SELECT id FROM ticket_sets WHERE title=?',[title]).fetchone():
        raise click.ClickException('Collection already exists; choose a new version title')
    collection = db.execute('INSERT INTO ticket_sets(title,source,is_published) VALUES (?,?,0)',[title,source]).lastrowid
    for ticket in tickets:
        db.execute('INSERT INTO oral_tickets(set_id,number,questions_json) VALUES (?,?,?)',
                   [collection,ticket['number'],json.dumps(ticket['questions'],ensure_ascii=False)])
    db.commit()
    click.echo('Draft collection ID: '+str(collection))


@app.cli.command('publish-tickets')
def publish_tickets():
    import click
    collection = click.prompt('Reviewed collection ID',type=int)
    db = get_db()
    if not db.execute('SELECT id FROM oral_tickets WHERE set_id=? LIMIT 1',[collection]).fetchone():
        raise click.ClickException('Collection empty or missing')
    if not click.confirm('Publish reviewed materials to PRO users?'):
        return
    db.execute('UPDATE ticket_sets SET is_published=1 WHERE id=?',[collection])
    db.commit()


@app.route('/tickets')
@login_required
def ticket_sets_page():
    user=get_user()
    if not has_pro(user):
        return redirect(url_for('exam_center'))
    db=get_db()
    sets=db.execute("""
        SELECT s.*, COUNT(t.id) AS total, COUNT(r.ticket_id) AS studied,
               SUM(CASE WHEN r.rating=3 THEN 1 ELSE 0 END) AS confident
        FROM ticket_sets s JOIN oral_tickets t ON t.set_id=s.id
        LEFT JOIN ticket_ratings r ON r.ticket_id=t.id AND r.user_id=?
        WHERE s.is_published=1 GROUP BY s.id ORDER BY s.title
    """,[user['id']]).fetchall()
    return render_template('tickets.html',user=user,stats=get_user_stats(user['id']),sets=sets)


@app.route('/tickets/set/<int:set_id>')
@login_required
def ticket_set_page(set_id):
    user=get_user()
    if not has_pro(user):
        return redirect(url_for('exam_center'))
    db=get_db()
    collection=db.execute('SELECT * FROM ticket_sets WHERE id=? AND is_published=1',[set_id]).fetchone()
    if not collection:
        return redirect(url_for('ticket_sets_page'))
    if request.args.get('draw') in ('random','weak'):
        weak=request.args.get('draw')=='weak'
        ticket=db.execute("""
            SELECT t.id FROM oral_tickets t LEFT JOIN ticket_ratings r
            ON r.ticket_id=t.id AND r.user_id=?
            WHERE t.set_id=? AND (?=0 OR r.rating IS NULL OR r.rating<3)
            ORDER BY RANDOM() LIMIT 1
        """,[user['id'],set_id,int(weak)]).fetchone()
        if ticket:
            return redirect(url_for('ticket_page',ticket_id=ticket['id']))
    tickets=db.execute("""
        SELECT t.id,t.number,r.rating,r.attempts FROM oral_tickets t
        LEFT JOIN ticket_ratings r ON r.ticket_id=t.id AND r.user_id=?
        WHERE t.set_id=? ORDER BY t.number
    """,[user['id'],set_id]).fetchall()
    return render_template('tickets.html',user=user,stats=get_user_stats(user['id']),collection=collection,tickets=tickets)


@app.route('/tickets/<int:ticket_id>',methods=['GET','POST'])
@login_required
def ticket_page(ticket_id):
    import secrets
    user=get_user()
    if not has_pro(user):
        return redirect(url_for('exam_center'))
    db=get_db()
    ticket=db.execute("""
        SELECT t.*,s.title,s.source FROM oral_tickets t JOIN ticket_sets s ON s.id=t.set_id
        WHERE t.id=? AND s.is_published=1
    """,[ticket_id]).fetchone()
    if not ticket:
        return redirect(url_for('ticket_sets_page'))
    if request.method=='POST':
        token=session.get('ticket_csrf','')
        supplied=request.form.get('csrf','')
        if not token or not secrets.compare_digest(token,supplied):
            return 'Invalid request',400
        rating=request.form.get('rating',type=int)
        if rating not in (1,2,3):
            return 'Invalid rating',400
        db.execute("""
            INSERT INTO ticket_ratings(user_id,ticket_id,rating) VALUES (?,?,?)
            ON CONFLICT(user_id,ticket_id) DO UPDATE SET rating=excluded.rating,
            attempts=attempts+1,updated_at=CURRENT_TIMESTAMP
        """,[user['id'],ticket_id,rating])
        db.commit()
        return redirect(url_for('ticket_set_page',set_id=ticket['set_id']))
    if not session.get('ticket_csrf'):
        session['ticket_csrf']=secrets.token_urlsafe(32)
    questions=json.loads(ticket['questions_json'])
    return render_template('tickets.html',user=user,stats=get_user_stats(user['id']),ticket=ticket,
                           questions=questions,revealed=request.args.get('show')=='1',csrf=session['ticket_csrf'])


@app.route('/exam-center')
@login_required
def exam_center():
    user = get_user()
    pro = has_pro(user)
    courses, sections, plan = [], [], []
    selected = None
    exam_date = request.args.get('date', '')
    minutes = request.args.get('minutes', 60, type=int)
    if minutes not in (30, 60, 120):
        minutes = 60
    days = None
    error = None
    if pro:
        db = get_db()
        courses = db.execute('SELECT id, name FROM courses WHERE is_active=1 ORDER BY order_index').fetchall()
        course_id = request.args.get('course_id', type=int)
        selected = next((c for c in courses if c['id'] == course_id), courses[0] if courses else None)
        if selected:
            sections = db.execute("""
                SELECT s.id, s.name, COUNT(DISTINCT l.id) AS total,
                       COUNT(DISTINCT CASE WHEN p.status='completed' THEN l.id END) AS completed
                FROM course_sections s
                LEFT JOIN course_topics t ON t.section_id=s.id
                LEFT JOIN course_levels l ON l.topic_id=t.id
                LEFT JOIN user_progress p ON p.level_id=l.id AND p.user_id=?
                WHERE s.course_id=? GROUP BY s.id ORDER BY s.order_index
            """, [user['id'], selected['id']]).fetchall()
        if exam_date:
            try:
                days = (datetime.date.fromisoformat(exam_date) - datetime.date.today()).days
                if not 1 <= days <= 365:
                    raise ValueError()
                pending = sorted((s for s in sections if s['total'] > s['completed']), key=lambda s: s['completed']/s['total'])
                # A planning heuristic, not a prediction of exam performance.
                slots = max(1, minutes // 30)
                for i, section in enumerate(pending[:days * slots]):
                    day = 1 + i // slots
                    plan.append({'day': day, 'section': section, 'minutes': 30})
            except ValueError:
                error = 'Выбери дату экзамена от завтра до одного года вперёд.'
                days = None
    return render_template('exam_center.html', user=user, stats=get_user_stats(user['id']),
                           pro=pro, courses=courses, selected=selected, sections=sections,
                           plan=plan, days=days, exam_date=exam_date, minutes=minutes, error=error)


@app.route('/exam')
@login_required
def exam_setup():
    user = get_user()
    if not has_pro(user):
        return redirect(url_for('exam_center'))
    course_id = request.args.get('course_id', type=int)
    return render_template('exam.html', user=user, stats=get_user_stats(user['id']), course_id=course_id, mode='mistakes' if request.args.get('mode') == 'mistakes' else 'exam')


@app.route('/api/exam/start', methods=['POST'])
@login_required
def api_exam_start():
    import secrets, time
    user = get_user()
    if not has_pro(user):
        return jsonify({'error': 'Требуется доступ PRO'}), 403
    db = get_db()
    course_id = request.args.get('course_id', type=int)
    mode = 'mistakes' if request.args.get('mode') == 'mistakes' else 'exam'
    rows = db.execute("""
        SELECT q.* FROM level_questions q
        JOIN course_levels l ON l.id=q.level_id
        JOIN course_topics t ON t.id=l.topic_id
        JOIN course_sections s ON s.id=t.section_id
        JOIN courses c ON c.id=s.course_id
        WHERE c.is_active=1 AND (? IS NULL OR c.id=?)
        AND (?='exam' OR EXISTS (SELECT 1 FROM exam_mistakes m
            WHERE m.user_id=? AND m.question_id=q.id AND m.resolved=0))
        ORDER BY RANDOM()
    """, [course_id, course_id, mode, user['id']]).fetchall()
    valid = []
    for q in rows:
        try:
            options = json.loads(q['options_json'])
        except (ValueError, TypeError):
            continue
        if not isinstance(options, list) or not all(isinstance(o, str) for o in options) or q['correct_answer'] not in options or len(options)<2:
            continue
        valid.append((q, options))
        if len(valid)==20:
            break
    if not valid:
        return jsonify({'error': 'Нет доступных вопросов'}), 404
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    attempt = db.execute('INSERT INTO exam_attempts(user_id,course_id,mode,token,started_at,total) VALUES (?,?,?,?,?,?)',
                         [user['id'],course_id,mode,token,now,len(valid)]).lastrowid
    for i,(q,options) in enumerate(valid):
        db.execute('INSERT INTO exam_attempt_questions(attempt_id,position,question_id,question,options_json,correct_answer,explanation) VALUES (?,?,?,?,?,?,?)',
                   [attempt,i,q['id'],q['question'],json.dumps(options,ensure_ascii=False),q['correct_answer'],q['explanation']])
    db.commit()
    return jsonify({'token':token,'seconds':900,'questions':[{'question':q['question'],'options':options} for q,options in valid]})


@app.route('/api/exam/finish', methods=['POST'])
@login_required
def api_exam_finish():
    import time
    user = get_user()
    if not has_pro(user):
        return jsonify({'error':'Требуется доступ PRO'}),403
    payload = request.get_json(silent=True)
    if not isinstance(payload,dict) or not isinstance(payload.get('token'),str) or not isinstance(payload.get('answers'),list):
        return jsonify({'error':'Некорректные данные'}),400
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    attempt = db.execute('SELECT * FROM exam_attempts WHERE token=? AND user_id=?',[payload['token'],user['id']]).fetchone()
    if not attempt:
        db.rollback()
        return jsonify({'error':'Попытка не найдена'}),404
    rows = db.execute('SELECT * FROM exam_attempt_questions WHERE attempt_id=? ORDER BY position',[attempt['id']]).fetchall()
    if attempt['finished_at'] is None:
        answers = payload['answers']
        now = int(time.time())
        if len(answers)!=len(rows) or any(v is not None and not isinstance(v,str) for v in answers):
            db.rollback()
            return jsonify({'error':'Некорректные ответы'}),400
        if now > attempt['started_at']+930:
            db.rollback()
            return jsonify({'error':'Время отправки истекло'}),409
        correct = 0
        for q,answer in zip(rows,answers):
            if answer is not None and answer not in json.loads(q['options_json']):
                db.rollback()
                return jsonify({'error':'Недопустимый вариант'}),400
            ok = int(answer == q['correct_answer'])
            correct += ok
            db.execute('UPDATE exam_attempt_questions SET selected_answer=?,is_correct=? WHERE attempt_id=? AND position=?',[answer,ok,attempt['id'],q['position']])
            if not ok:
                db.execute("""
                    INSERT INTO exam_mistakes(user_id,question_id,mistakes,resolved)
                    SELECT ?,id,1,0 FROM level_questions WHERE id=?
                    ON CONFLICT(user_id,question_id) DO UPDATE SET mistakes=mistakes+1,resolved=0
                """,[user['id'],q['question_id']])
            else:
                db.execute('UPDATE exam_mistakes SET resolved=1 WHERE user_id=? AND question_id=?',[user['id'],q['question_id']])
        db.execute('UPDATE exam_attempts SET finished_at=?,correct=? WHERE id=?',[now,correct,attempt['id']])
        db.commit()
    else:
        correct = attempt['correct']
        db.rollback()
    return jsonify({'correct':correct,'total':attempt['total'],'percent':round(100*correct/attempt['total']),'attempt_id':attempt['id']})


@app.route('/exam-history')
@login_required
def exam_history():
    user=get_user()
    if not has_pro(user):
        return redirect(url_for('exam_center'))
    db=get_db()
    history=db.execute("""SELECT a.*,c.name AS course_name,datetime(a.finished_at,'unixepoch') AS date
        FROM exam_attempts a LEFT JOIN courses c ON c.id=a.course_id
        WHERE a.user_id=? AND a.finished_at IS NOT NULL ORDER BY a.finished_at DESC LIMIT 50""",[user['id']]).fetchall()
    mistakes=db.execute("""SELECT m.*,q.question,t.name AS topic_name
        FROM exam_mistakes m JOIN level_questions q ON q.id=m.question_id
        JOIN course_levels l ON l.id=q.level_id JOIN course_topics t ON t.id=l.topic_id
        WHERE m.user_id=? AND m.resolved=0 ORDER BY m.mistakes DESC LIMIT 100""",[user['id']]).fetchall()
    return render_template('exam_history.html',user=user,stats=get_user_stats(user['id']),history=history,mistakes=mistakes)


@app.route('/exam-history/<int:attempt_id>')
@login_required
def exam_attempt_detail(attempt_id):
    user=get_user()
    if not has_pro(user):
        return redirect(url_for('exam_center'))
    db=get_db()
    attempt=db.execute('SELECT * FROM exam_attempts WHERE id=? AND user_id=? AND finished_at IS NOT NULL',[attempt_id,user['id']]).fetchone()
    if not attempt:
        return redirect(url_for('exam_history'))
    questions=db.execute('SELECT * FROM exam_attempt_questions WHERE attempt_id=? ORDER BY position',[attempt_id]).fetchall()
    return render_template('exam_history.html',user=user,stats=get_user_stats(user['id']),attempt=attempt,questions=questions)


# ==================== ПОВТОРЕНИЕ ====================

@app.route('/reviews')
@login_required
def reviews_page():
    user = get_user()
    db = get_db()
    stats = get_user_stats(user['id'])
    reviews = get_reviews_to_do(user['id'])
    
    return render_template('reviews.html', 
                           user=user, stats=stats, 
                           reviews=reviews)


# ==================== ЛИДЕРБОРД ====================

@app.route('/leaderboard')
@login_required
def leaderboard():
    user = get_user()
    db = get_db()
    stats = get_user_stats(user['id'])
    
    top_users = db.execute('''
        SELECT u.username, u.avatar, s.weekly_xp, s.level, s.streak
        FROM users u
        JOIN user_stats s ON u.id = s.user_id
        WHERE s.weekly_xp > 0
        ORDER BY s.weekly_xp DESC
        LIMIT 20
    ''').fetchall()

    return render_template('leaderboard.html',
                           user=user, stats=stats,
                           top_users=top_users)


# ==================== ПРОФИЛЬ ====================

@app.route('/profile')
@login_required
def profile():
    user = get_user()
    db = get_db()
    stats = get_user_stats(user['id'])
    user_courses = db.execute('''
        SELECT c.* FROM courses c
        JOIN user_courses uc ON uc.course_id=c.id
        WHERE uc.user_id=?
    ''', [user['id']]).fetchall()
    
    all_achievements = db.execute('SELECT * FROM achievements ORDER BY id').fetchall()
    earned_rows = db.execute('SELECT achievement_id FROM user_achievements WHERE user_id=?', [user['id']]).fetchall()
    earned_ids = [r['achievement_id'] for r in earned_rows]

    return render_template('profile.html', user=user, stats=stats,
                           user_courses=user_courses,
                           all_achievements=all_achievements,
                           earned_ids=earned_ids,
                           week=get_week_activity(user['id']))


# ==================== ADMIN ====================
from admin_panel import register_admin
register_admin(app, get_user, get_user_stats, get_db, has_pro)


# ==================== API ====================

@app.route('/api/stats')
@login_required
def api_stats():
    user = get_user()
    stats = get_user_stats(user['id'])
    return jsonify(dict(stats))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

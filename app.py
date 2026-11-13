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
            # Если с последнего сброса прошло 7 и более дней
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
        user = db.execute('SELECT * FROM users WHERE username=? AND password=?',
                          [username, hash_password(password)]).fetchone()

        if not user:
            return render_template('login.html', error='Неверный логин или пароль')

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
    session.pop('user_id', None)
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
               (SELECT COUNT(*) FROM course_sections WHERE course_id=c.id) as sections_count
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

    return render_template('dashboard.html',
                           user=user, stats=stats,
                           user_courses=user_courses,
                           all_courses=all_courses,
                           daily=daily)


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
            
            comp_res = db.execute(f'''
                SELECT COUNT(*) as c FROM user_progress 
                WHERE user_id=? AND status='completed' 
                AND level_id IN (SELECT id FROM course_levels WHERE topic_id IN ({placeholders}))
            ''', [user['id']] + topic_ids).fetchone()
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

        completed_res = db.execute('''
            SELECT COUNT(*) as c FROM user_progress
            WHERE user_id=? AND status='completed'
            AND level_id IN (SELECT id FROM course_levels WHERE topic_id=?)
        ''', [user['id'], t['id']]).fetchone()
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
            'question': q['question'],
            'options': options,
            'correct_answer': q['correct_answer'],
            'explanation': q['explanation'] or '',
            'question_type': q['question_type'],
        })

    topic = db.execute('SELECT * FROM course_topics WHERE id=?', [level['topic_id']]).fetchone()

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

    existing = db.execute('SELECT * FROM user_progress WHERE user_id=? AND level_id=?',
                          [user['id'], level_id]).fetchone()

    if existing:
        best_stars = max(existing['stars'], stars)
        best_score = max(existing['best_score'], percent)
        new_status = 'completed' if best_stars >= 1 else existing['status']

        db.execute('''
            UPDATE user_progress
            SET stars=?, best_score=?, attempts=attempts+1, status=?, updated_at=CURRENT_TIMESTAMP,
                completed_at=CASE WHEN ?='completed' THEN CURRENT_TIMESTAMP ELSE completed_at END
            WHERE user_id=? AND level_id=?
        ''', [best_stars, best_score, new_status, new_status, user['id'], level_id])
    else:
        new_status = 'completed' if stars >= 1 else 'unlocked'
        db.execute('''
            INSERT INTO user_progress (user_id, level_id, status, stars, attempts, best_score, completed_at)
            VALUES (?,?,?,?,1,?, CASE WHEN ?='completed' THEN CURRENT_TIMESTAMP ELSE NULL END)
        ''', [user['id'], level_id, new_status, stars, percent, new_status])

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
                           earned_ids=earned_ids)


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

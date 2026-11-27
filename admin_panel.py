"""Role-gated administration for MechLingo."""
import csv
import datetime
import io
import json
import os
import secrets
import sqlite3
from functools import wraps
from flask import abort, flash, redirect, render_template, request, session, url_for

# Fixed identifiers only; never interpolate user-supplied SQL identifiers.
CONTENT = {
    'courses': ('courses', 'Дисциплины', None, None),
    'sections': ('course_sections', 'Разделы', 'course_id', 'courses'),
    'topics': ('course_topics', 'Темы', 'section_id', 'course_sections'),
    'levels': ('course_levels', 'Уроки', 'topic_id', 'course_topics'),
}


def register_admin(app, get_user, get_user_stats, get_db, has_pro):
    def admin_required(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # Refuse privileged access unless a production signing key is configured.
            configured_key = os.environ.get('SECRET_KEY', '')
            if len(configured_key) < 48:
                abort(503, description='Configure a strong SECRET_KEY before using administration.')
            user = get_user()
            if not user:
                return redirect(url_for('login'))
            if user['role'] != 'admin':
                abort(403)
            if request.method == 'POST':
                token = session.get('admin_csrf', '')
                supplied = request.form.get('csrf', '')
                if not token or not secrets.compare_digest(token, supplied):
                    abort(400)
            return fn(*args, **kwargs)
        return wrapper

    def page(tab, **kwargs):
        user = get_user()
        if not session.get('admin_csrf'):
            session['admin_csrf'] = secrets.token_urlsafe(32)
        return render_template('admin_panel.html', user=user, stats=get_user_stats(user['id']),
                               csrf=session['admin_csrf'], tab=tab, **kwargs)

    def upload(field, limit=2_000_000):
        file = request.files.get(field)
        if not file or not file.filename:
            raise ValueError('Выбери файл.')
        raw = file.stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('Максимальный размер файла — 2 МБ.')
        return raw.decode('utf-8-sig')

    @app.route('/admin/setup', methods=['GET', 'POST'])
    def admin_setup():
        # Bootstrap only an authenticated, explicitly configured account.
        configured_key = os.environ.get('SECRET_KEY', '')
        username = os.environ.get('ADMIN_USERNAME', '').strip()
        setup_token = os.environ.get('ADMIN_SETUP_TOKEN', '')
        if len(configured_key) < 48 or not username or len(setup_token) < 48:
            abort(404)
        user = get_user()
        if not user:
            return redirect(url_for('login'))
        db = get_db()
        if user['username'] != username:
            abort(404)
        if db.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone():
            return redirect(url_for('admin_home')) if user['role'] == 'admin' else abort(404)
        if not session.get('admin_setup_csrf'):
            session['admin_setup_csrf'] = secrets.token_urlsafe(32)
        error = None
        if request.method == 'POST':
            csrf = request.form.get('csrf', '')
            supplied = request.form.get('setup_token', '')
            if not secrets.compare_digest(session['admin_setup_csrf'], csrf):
                abort(400)
            if not secrets.compare_digest(setup_token.encode('utf-8'), supplied.encode('utf-8')):
                error = 'Код активации неверный.'
            else:
                db.execute('BEGIN IMMEDIATE')
                result = db.execute("""
                    UPDATE users SET role='admin'
                    WHERE id=? AND username=? AND NOT EXISTS
                    (SELECT 1 FROM users WHERE role='admin')
                """, [user['id'], username])
                db.commit()
                if not result.rowcount:
                    abort(409)
                session.pop('admin_setup_csrf', None)
                app.logger.info('Initial administrator setup completed')
                return redirect(url_for('admin_home'))
        response = app.make_response(render_template(
            'admin_setup.html', user=user, stats=get_user_stats(user['id']),
            csrf=session['admin_setup_csrf'], error=error))
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.cli.command('grant-admin')
    def grant_admin():
        import click
        username = click.prompt('Username')
        if not click.confirm('Grant full administrator permissions?'):
            return
        db = get_db()
        result = db.execute("UPDATE users SET role='admin' WHERE username=?", [username])
        if not result.rowcount:
            raise click.ClickException('User not found')
        db.commit()
        click.echo('Administrator role granted.')

    @app.route('/admin')
    @admin_required
    def admin_home():
        db = get_db()
        counts = {
            'users': db.execute('SELECT COUNT(*) FROM users').fetchone()[0],
            'pro': db.execute('SELECT COUNT(*) FROM users WHERE pro_until>=?', [datetime.date.today().isoformat()]).fetchone()[0],
            'courses': db.execute('SELECT COUNT(*) FROM courses').fetchone()[0],
            'questions': db.execute('SELECT COUNT(*) FROM level_questions').fetchone()[0],
            'tickets': db.execute('SELECT COUNT(*) FROM oral_tickets').fetchone()[0],
            'exams': db.execute('SELECT COUNT(*) FROM exam_attempts WHERE finished_at IS NOT NULL').fetchone()[0],
        }
        return page('home', counts=counts)

    @app.route('/admin/content/<kind>', methods=['GET', 'POST'])
    @admin_required
    def admin_content(kind):
        if kind not in CONTENT:
            abort(404)
        table, title, parent_col, parent_table = CONTENT[kind]
        db = get_db()
        parent_id = request.args.get('parent', type=int)
        if request.method == 'POST':
            try:
                name = request.form.get('name', '').strip()
                if not name or len(name)>250:
                    raise ValueError('Название: от 1 до 250 символов.')
                item_id = request.form.get('id', type=int)
                order = request.form.get('order_index', type=int)
                if order is None or not 0 <= order <= 100000:
                    raise ValueError('Укажи корректный порядок.')
                fields = {'name': name, 'order_index': order}
                if kind != 'levels':
                    fields['description'] = request.form.get('description','').strip()
                if parent_col:
                    parent_id = request.form.get('parent', type=int)
                    if not db.execute(f'SELECT id FROM {parent_table} WHERE id=?',[parent_id]).fetchone():
                        raise ValueError('Выбери существующий родительский раздел.')
                    # Never move existing material and associated student progress.
                    if not item_id:
                        fields[parent_col] = parent_id
                if item_id:
                    existing = db.execute(f'SELECT * FROM {table} WHERE id=?',[item_id]).fetchone()
                    if not existing or (parent_col and existing[parent_col]!=parent_id):
                        raise ValueError('Запись не найдена в выбранном разделе.')
                    db.execute(f"UPDATE {table} SET "+','.join(k+'=?' for k in fields)+' WHERE id=?', [*fields.values(),item_id])
                else:
                    db.execute(f"INSERT INTO {table} ("+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')', list(fields.values()))
                db.commit()
                flash('Изменения сохранены.', 'success')
            except (ValueError, sqlite3.Error) as exc:
                db.rollback()
                flash(str(exc) if isinstance(exc,ValueError) else 'Не удалось сохранить запись.', 'error')
            return redirect(url_for('admin_content',kind=kind,parent=parent_id))
        parents = db.execute(f'SELECT id,name FROM {parent_table} ORDER BY name,id').fetchall() if parent_col else []
        if parent_col and parent_id is None and parents:
            parent_id = parents[0]['id']
        rows = db.execute(f'SELECT * FROM {table} WHERE {parent_col}=? ORDER BY order_index,id',[parent_id]).fetchall() if parent_col else db.execute(f'SELECT * FROM {table} ORDER BY order_index,id').fetchall()
        edit_id = request.args.get('edit',type=int)
        edit = next((r for r in rows if r['id']==edit_id),None)
        return page('content',kind=kind,title=title,parents=parents,parent=parent_id,rows=rows,edit=edit,has_parent=bool(parent_col))

    @app.route('/admin/questions', methods=['GET','POST'])
    @admin_required
    def admin_questions():
        db=get_db()
        level_id=request.args.get('level',type=int)
        if request.method=='POST':
            level_id=request.form.get('level',type=int)
            try:
                if not db.execute('SELECT id FROM course_levels WHERE id=?',[level_id]).fetchone():
                    raise ValueError('Выбери существующий урок.')
                question=request.form.get('question','').strip()
                options=[o.strip() for o in request.form.get('options','').splitlines() if o.strip()]
                correct=request.form.get('correct','').strip()
                explanation=request.form.get('explanation','').strip()
                if not question or len(options)<2 or len(options)>8 or len(set(options))!=len(options) or correct not in options:
                    raise ValueError('Нужны вопрос, 2–8 уникальных вариантов и правильный ответ, совпадающий с одним из вариантов.')
                values=[question,json.dumps(options,ensure_ascii=False),correct,explanation]
                item_id=request.form.get('id',type=int)
                if item_id:
                    result=db.execute('UPDATE level_questions SET question=?,options_json=?,correct_answer=?,explanation=? WHERE id=? AND level_id=?',values+[item_id,level_id])
                    if not result.rowcount:
                        raise ValueError('Вопрос не найден.')
                else:
                    db.execute('INSERT INTO level_questions(question,options_json,correct_answer,explanation,level_id) VALUES (?,?,?,?,?)',values+[level_id])
                db.commit()
                flash('Вопрос сохранён.','success')
            except (ValueError,sqlite3.Error) as exc:
                db.rollback()
                flash(str(exc) if isinstance(exc,ValueError) else 'Ошибка сохранения.','error')
            return redirect(url_for('admin_questions',level=level_id))
        levels=db.execute("""SELECT l.id,c.name||' / '||s.name||' / '||t.name||' / '||l.name AS name
            FROM course_levels l JOIN course_topics t ON t.id=l.topic_id
            JOIN course_sections s ON s.id=t.section_id JOIN courses c ON c.id=s.course_id
            ORDER BY c.name,s.order_index,t.order_index,l.order_index,l.id""").fetchall()
        if level_id is None and levels:
            level_id=levels[0]['id']
        rows=db.execute('SELECT * FROM level_questions WHERE level_id=? ORDER BY order_index,id',[level_id]).fetchall()
        edit=next((r for r in rows if r['id']==request.args.get('edit',type=int)),None)
        return page('questions',levels=levels,level=level_id,rows=rows,edit=edit,options='\n'.join(json.loads(edit['options_json'])) if edit else '')

    @app.route('/admin/import',methods=['GET','POST'])
    @admin_required
    def admin_import():
        if request.method=='POST':
            db=get_db()
            try:
                reader=csv.reader(io.StringIO(upload('csv_file')),delimiter=';')
                next(reader,None)
                count=0
                for line,row in enumerate(reader,2):
                    if not row or not any(row):
                        continue
                    if len(row) not in (7,8):
                        raise ValueError(f'Строка {line}: ожидается 7 или 8 колонок.')
                    level=int(row[0])
                    question=row[1].strip()
                    options=[v.strip() for v in row[2:6] if v.strip()]
                    correct=row[6].strip()
                    if not question or len(options)<2 or len(set(options))!=len(options) or correct not in options or not db.execute('SELECT id FROM course_levels WHERE id=?',[level]).fetchone():
                        raise ValueError(f'Строка {line}: проверь урок, вопрос и варианты.')
                    db.execute('INSERT INTO level_questions(level_id,question,options_json,correct_answer,explanation) VALUES (?,?,?,?,?)',[level,question,json.dumps(options,ensure_ascii=False),correct,row[7].strip() if len(row)==8 else ''])
                    count+=1
                if not count:
                    raise ValueError('Файл не содержит вопросов.')
                db.commit()
                flash(f'Добавлено вопросов: {count}.','success')
            except (ValueError,UnicodeError,csv.Error,sqlite3.Error) as exc:
                db.rollback()
                flash(str(exc) if isinstance(exc,ValueError) else 'Ошибка импорта. Изменения отменены.','error')
            return redirect(url_for('admin_import'))
        return page('import')

    @app.route('/admin/tickets',methods=['GET','POST'])
    @admin_required
    def admin_tickets():
        db=get_db()
        if request.method=='POST':
            try:
                action=request.form.get('action')
                if action=='import':
                    data=json.loads(upload('json_file'))
                    title,source,tickets=data['title'].strip(),data['source'].strip(),data['tickets']
                    if not title or not source or not isinstance(tickets,list) or not tickets:
                        raise ValueError('Нужны название, источник и билеты.')
                    seen=set()
                    for t in tickets:
                        n=t['number']
                        if type(n) is not int or n<1 or n in seen:
                            raise ValueError('Номера должны быть уникальными положительными числами.')
                        seen.add(n)
                        questions=t['questions']
                        if not isinstance(questions,list) or not questions or any(not isinstance(q,dict) or not all(isinstance(q.get(k),str) and q[k].strip() for k in ('question','answer')) for q in questions):
                            raise ValueError('У каждого билета должны быть вопросы и ответы.')
                    collection=db.execute('INSERT INTO ticket_sets(title,source,is_published) VALUES (?,?,0)',[title,source]).lastrowid
                    for t in tickets:
                        db.execute('INSERT INTO oral_tickets(set_id,number,questions_json) VALUES (?,?,?)',[collection,t['number'],json.dumps(t['questions'],ensure_ascii=False)])
                elif action in ('publish','unpublish'):
                    collection=request.form.get('id',type=int)
                    if action=='publish' and (request.form.get('reviewed')!='yes' or not db.execute('SELECT id FROM oral_tickets WHERE set_id=?',[collection]).fetchone()):
                        raise ValueError('Подтверди проверку материалов непустого комплекта.')
                    db.execute('UPDATE ticket_sets SET is_published=? WHERE id=?',[int(action=='publish'),collection])
                else:
                    raise ValueError('Неизвестное действие.')
                db.commit()
                flash('Изменения сохранены.','success')
            except (ValueError,TypeError,KeyError,AttributeError,sqlite3.Error) as exc:
                db.rollback()
                flash(str(exc) if isinstance(exc,ValueError) else 'Проверь формат и уникальность названия комплекта.','error')
            return redirect(url_for('admin_tickets'))
        sets=db.execute('SELECT s.*,COUNT(t.id) AS total FROM ticket_sets s LEFT JOIN oral_tickets t ON t.set_id=s.id GROUP BY s.id ORDER BY s.id DESC').fetchall()
        selected=request.args.get('view',type=int)
        preview=db.execute('SELECT * FROM oral_tickets WHERE set_id=? ORDER BY number',[selected]).fetchall() if selected else []
        return page('tickets',sets=sets,preview=[{'number':r['number'],'questions':json.loads(r['questions_json'])} for r in preview],selected=selected)

    @app.route('/admin/users',methods=['GET','POST'])
    @admin_required
    def admin_users():
        db=get_db()
        if request.method=='POST':
            uid=request.form.get('id',type=int)
            days=request.form.get('days',type=int)
            if days is None or not 0<=days<=366:
                abort(400)
            expiry=(datetime.date.today()+datetime.timedelta(days=days-1)).isoformat() if days else None
            result=db.execute('UPDATE users SET pro_until=? WHERE id=?',[expiry,uid])
            db.commit()
            flash('Срок доступа обновлён.' if result.rowcount else 'Пользователь не найден.','success' if result.rowcount else 'error')
            return redirect(url_for('admin_users',q=request.form.get('q','')))
        query=request.args.get('q','').strip()[:150]
        users=db.execute('SELECT id,username,role,pro_until,created_at FROM users WHERE instr(lower(username),lower(?))>0 OR ?=\'\' ORDER BY id DESC LIMIT 100',[query,query]).fetchall()
        return page('users',users=[dict(u,pro=has_pro(u)) for u in users],query=query)

from datetime import date, timedelta
from models import db, User, DailyActivity


def award_xp(user, amount):
    """
    Начисляет XP, обновляет дневную активность, проверяет цель и уровень.
    Возвращает dict с событиями для фронтенда.
    """
    events = {
        "xp_gained": amount,
        "goal_reached": False,
        "level_up": False,
        "new_level": user.level,
        "streak": user.streak,
        "total_xp": 0,
    }

    # 1. Начисляем общий XP
    user.xp = (user.xp or 0) + amount
    events["total_xp"] = user.xp

    # 2. Записываем в активность за сегодня
    activity = user.today_activity()
    activity.xp_earned += amount

    # 3. Проверяем цель дня
    if not activity.goal_reached and activity.xp_earned >= user.daily_goal:
        activity.goal_reached = True
        events["goal_reached"] = True
        # Стрик продлевается один раз в день при достижении цели
        _update_streak(user)

    # 4. Проверяем level up
    while user.xp >= user.xp_for_next_level():
        user.level += 1
        events["level_up"] = True

    events["new_level"] = user.level
    events["streak"] = user.streak

    db.session.commit()
    return events


def _update_streak(user):
    """
    Продлевает стрик. Вызывается, когда пользователь выполнил дневную цель.
    """
    today = date.today()
    last = user.last_activity_date

    if last == today:
        return  # уже обновляли сегодня

    if last == today - timedelta(days=1):
        # занимались вчера — стрик растёт
        user.streak = (user.streak or 0) + 1
    else:
        # пропуск — стрик сбрасывается на 1
        user.streak = 1

    user.last_activity_date = today


def check_and_reset_streak(user):
    """
    Вызывается при загрузке дашборда/любой страницы.
    Если пользователь пропустил вчера — стрик обнуляется.
    """
    if not user.last_activity_date:
        return

    today = date.today()
    delta = (today - user.last_activity_date).days

    # Пропустил более одного дня (вчера не заходил) — стрик сгорает
    if delta > 1:
        user.streak = 0
        db.session.commit()


def get_week_activity(user):
    """Последние 7 дней для виджета календаря на дашборде."""
    today = date.today()
    start = today - timedelta(days=6)
    rows = user.activities.filter(DailyActivity.date >= start).all()
    by_date = {r.date: r for r in rows}

    week = []
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    for i in range(7):
        d = start + timedelta(days=i)
        act = by_date.get(d)
        week.append({
            "date": d,
            "day_name": day_names[d.weekday()],
            "xp": act.xp_earned if act else 0,
            "goal_reached": act.goal_reached if act else False,
            "is_today": d == today,
        })
    return week


def get_daily_progress(user):
    """Данные для прогресс-бара цели дня."""
    activity = user.today_activity()
    db.session.commit()
    return {
        "xp_today": activity.xp_earned,
        "goal": user.daily_goal,
        "percent": min(100, int(activity.xp_earned / user.daily_goal * 100)),
        "goal_reached": activity.goal_reached,
        "week": get_week_activity(user),
    }

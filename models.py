from datetime import datetime, date, timedelta
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    # Геймификация
    xp = db.Column(db.Integer, default=0)
    hearts = db.Column(db.Integer, default=5)
    level = db.Column(db.Integer, default=1)
    streak = db.Column(db.Integer, default=0)
    last_activity_date = db.Column(db.Date, nullable=True)

    # Ежедневная цель (в XP)
    daily_goal = db.Column(db.Integer, default=50)

    activities = db.relationship('DailyActivity', backref='user', lazy='dynamic',
                                 cascade='all, delete-orphan')

    def xp_for_next_level(self):
        """Порог опыта для следующего уровня: 100, 200, 300..."""
        return self.level * 100

    def today_activity(self):
        """Получить/создать запись активности на сегодня."""
        today = date.today()
        activity = self.activities.filter_by(date=today).first()
        if not activity:
            activity = DailyActivity(user_id=self.id, date=today, xp_earned=0)
            db.session.add(activity)
            db.session.flush()
        return activity


class DailyActivity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False, default=date.today)
    xp_earned = db.Column(db.Integer, default=0)
    lessons_completed = db.Column(db.Integer, default=0)
    goal_reached = db.Column(db.Boolean, default=False)
    goal_celebrated = db.Column(db.Boolean, default=False)  # чтобы не спамить тостом

    __table_args__ = (db.UniqueConstraint('user_id', 'date', name='uq_user_date'),)

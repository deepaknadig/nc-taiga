from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()

class Config(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nextcloud_url = db.Column(db.String(255), nullable=True)
    nextcloud_username = db.Column(db.String(255), nullable=True)
    nextcloud_app_password = db.Column(db.String(255), nullable=True)
    nextcloud_task_list = db.Column(db.String(255), nullable=True)

    taiga_url = db.Column(db.String(255), nullable=True)
    taiga_username = db.Column(db.String(255), nullable=True)
    taiga_password = db.Column(db.String(255), nullable=True)

    # Store IDs since we select from dropdowns
    taiga_project_id = db.Column(db.Integer, nullable=True)
    taiga_project_slug = db.Column(db.String(255), nullable=True) # useful for display/fallback
    taiga_user_story_id = db.Column(db.Integer, nullable=True)
    taiga_user_story_ref = db.Column(db.Integer, nullable=True) # useful for display

    last_sync_time = db.Column(db.DateTime, nullable=True)
    setup_time = db.Column(db.DateTime, nullable=True)

class TaskMapping(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nextcloud_task_uid = db.Column(db.String(255), unique=True, nullable=False)
    taiga_task_id = db.Column(db.Integer, unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class SyncLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()

class GlobalConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nextcloud_url = db.Column(db.String(255), nullable=True)
    nextcloud_username = db.Column(db.String(255), nullable=True)
    nextcloud_app_password = db.Column(db.String(255), nullable=True)

    taiga_url = db.Column(db.String(255), nullable=True)
    taiga_username = db.Column(db.String(255), nullable=True)
    taiga_password = db.Column(db.String(255), nullable=True)

class SyncConnection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nextcloud_task_list = db.Column(db.String(255), nullable=False) # display name
    nextcloud_task_list_id = db.Column(db.String(255), nullable=False) # url/id

    taiga_project_id = db.Column(db.Integer, nullable=False)
    taiga_project_slug = db.Column(db.String(255), nullable=False)
    taiga_user_story_id = db.Column(db.Integer, nullable=True) # can be null if syncing whole project
    taiga_user_story_ref = db.Column(db.Integer, nullable=True)

    setup_time = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    last_sync_time = db.Column(db.DateTime, nullable=True)

class TaskMapping(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(db.Integer, db.ForeignKey('sync_connection.id', ondelete='CASCADE'), nullable=False)
    nextcloud_task_uid = db.Column(db.String(255), nullable=False) # Not unique globally, only per connection if a task exists in multiple lists (unlikely but possible)
    taiga_task_id = db.Column(db.Integer, nullable=False)

    # Cache state to easily check what changed without complex diffs
    last_known_taiga_status = db.Column(db.Boolean, default=False) # is_closed
    last_known_taiga_subject = db.Column(db.String(255), nullable=True)
    last_known_taiga_description = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class SyncLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(db.Integer, db.ForeignKey('sync_connection.id', ondelete='CASCADE'), nullable=True)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)

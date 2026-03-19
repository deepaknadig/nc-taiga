from flask import Flask, render_template, request, redirect, url_for, flash
from database import init_db
from models import db, Config, SyncLog, TaskMapping
import os

app = Flask(__name__)
# Absolute path to the database to avoid path issues
db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance', 'config.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'some-secret-key-for-flask-flash-messages'

# Ensure the instance folder exists
os.makedirs(os.path.dirname(db_path), exist_ok=True)

init_db(app)

from sync import mark_nextcloud_task_completed, update_nextcloud_task_details, register_taiga_webhook
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/')
def index():
    return redirect(url_for('config_page'))

@app.route('/config', methods=['GET', 'POST'])
def config_page():
    config = Config.query.first()

    if request.method == 'POST':
        try:
            # Check if this is the first setup
            is_new_setup = config.setup_time is None

            config.nextcloud_url = request.form['nextcloud_url']
            config.nextcloud_username = request.form['nextcloud_username']
            config.nextcloud_app_password = request.form['nextcloud_app_password']
            config.nextcloud_task_list = request.form['nextcloud_task_list']

            config.taiga_url = request.form['taiga_url']
            config.taiga_token = request.form['taiga_token']
            config.taiga_project_slug = request.form['taiga_project_slug']
            config.taiga_user_story_ref = int(request.form['taiga_user_story_ref'])

            if is_new_setup:
                from datetime import datetime, timezone
                config.setup_time = datetime.now(timezone.utc)

            db.session.commit()
            flash('Configuration saved successfully!', 'success')

            # Automatically register webhook in Taiga
            # request.host_url gives us e.g. "http://localhost:5000/" or public address
            success, message = register_taiga_webhook(config, request.host_url)
            if success:
                flash(message, 'success')
            else:
                flash(message, 'warning')

        except ValueError:
            flash('User Story Ref must be an integer.', 'error')
        except Exception as e:
            flash(f'An error occurred: {e}', 'error')
        return redirect(url_for('config_page'))

    return render_template('config.html', config=config)

@app.route('/status')
def status_page():
    config = Config.query.first()
    logs = SyncLog.query.order_by(SyncLog.timestamp.desc()).limit(20).all()
    return render_template('status.html', config=config, logs=logs)

@app.route('/taiga-webhook', methods=['POST'])
def taiga_webhook():
    """
    Webhook endpoint to receive task updates from Taiga.
    We check if a task was closed, and if so, we mark it as completed in Nextcloud.
    We also sync title/description updates.
    """
    config = Config.query.first()
    if not config or not config.taiga_project_slug:
        return {"status": "ignored", "message": "Taiga project not configured"}, 200

    payload = request.json
    if not payload:
        return {"status": "error", "message": "Invalid JSON"}, 400

    action = payload.get('action')
    obj_type = payload.get('type')

    # We only care about Tasks
    if obj_type != 'task':
        return {"status": "ignored", "message": "Not a task"}, 200

    data = payload.get('data', {})

    # The webhook payload might contain the User Story ID, but often the
    # reference we configured (`config.taiga_user_story_ref`) is a ref string/number,
    # not the internal ID. Rather than relying on potentially mismatched US IDs
    # from the webhook, we solely rely on our TaskMapping database to determine
    # if this is a task we are actively syncing.

    taiga_task_id = data.get('id')
    if not taiga_task_id:
        return {"status": "error", "message": "No task ID in payload"}, 400

    # Look up the task mapping
    mapping = TaskMapping.query.filter_by(taiga_task_id=taiga_task_id).first()
    if not mapping:
        # Not a task we track (or created manually in Taiga)
        return {"status": "ignored", "message": "Unmapped task"}, 200

    if action == 'change':
        change = payload.get('change', {})
        diff = change.get('diff', {})

        # Check if status changed
        status_changed = 'status' in diff
        if status_changed:
            new_status = diff['status'][1] # Assuming diff format is [old_val, new_val]
            # In Taiga, tasks are often considered done if their status slug is 'closed' or 'done' or is_closed=True
            # For simplicity, we can check if the new status implies closed
            is_closed = data.get('is_closed', False)
            if is_closed:
                mark_nextcloud_task_completed(config, mapping.nextcloud_task_uid)

        # Check if title or description changed
        title_changed = 'subject' in diff
        desc_changed = 'description_diff' in diff or 'description' in diff

        if title_changed or desc_changed:
            new_title = data.get('subject')
            new_desc = data.get('description')
            update_nextcloud_task_details(config, mapping.nextcloud_task_uid, new_title, new_desc)

    return {"status": "ok"}, 200

from apscheduler.schedulers.background import BackgroundScheduler
import sync

def run_sync_job():
    sync.sync_nextcloud_to_taiga(app)

scheduler = BackgroundScheduler()
scheduler.add_job(func=run_sync_job, trigger="interval", seconds=30)
scheduler.start()

if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0', use_reloader=False)

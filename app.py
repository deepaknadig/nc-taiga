from flask import Flask, render_template, request, redirect, url_for, flash, session
from database import init_db
from models import db, Config, SyncLog, TaskMapping
import os
from datetime import datetime, timezone
import logging

app = Flask(__name__)
# Absolute path to the database to avoid path issues
db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance', 'config.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'some-secret-key-for-flask-flash-messages'

# Ensure the instance folder exists
os.makedirs(os.path.dirname(db_path), exist_ok=True)

init_db(app)

from sync import mark_nextcloud_task_completed, update_nextcloud_task_details, register_taiga_webhook, get_taiga_api

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/')
def index():
    return redirect(url_for('config_page'))

@app.route('/config', methods=['GET'])
def config_page():
    config = Config.query.first()
    return render_template('config.html', config=config)

@app.route('/config/step1', methods=['POST'])
def config_step1():
    config = Config.query.first()
    if not config:
        config = Config()
        db.session.add(config)

    config.nextcloud_url = request.form['nextcloud_url']
    config.nextcloud_username = request.form['nextcloud_username']
    config.nextcloud_app_password = request.form['nextcloud_app_password']
    config.nextcloud_task_list = request.form['nextcloud_task_list']

    config.taiga_url = request.form['taiga_url']
    config.taiga_username = request.form['taiga_username']
    config.taiga_password = request.form['taiga_password']

    db.session.commit()

    # Try to connect and fetch projects
    try:
        api = get_taiga_api(config)
        projects = api.projects.list()

        # We need a fallback if list() fails or user isn't assigned properly,
        # but usually .list() gets projects the user is a member of.
        # Alternatively api.projects.list(member=api.me().id)
        if not projects:
            flash("Logged in successfully, but no projects found for this user.", "warning")

        return render_template('config_step2.html', config=config, projects=projects)

    except Exception as e:
        logger.error(f"Taiga Auth Error: {e}")
        flash(f"Failed to authenticate with Taiga: {e}", "error")
        return redirect(url_for('config_page'))

@app.route('/config/step2', methods=['POST'])
def config_step2():
    config = Config.query.first()

    try:
        project_id = int(request.form.get('taiga_project_id'))
        project_slug = request.form.get('taiga_project_slug')

        config.taiga_project_id = project_id
        config.taiga_project_slug = project_slug
        db.session.commit()

        # Fetch user stories for this project
        api = get_taiga_api(config)
        user_stories = api.user_stories.list(project=project_id)

        return render_template('config_step3.html', config=config, user_stories=user_stories)

    except Exception as e:
        flash(f"Error fetching user stories: {e}", "error")
        # Need to re-fetch projects if we go back
        try:
             api = get_taiga_api(config)
             projects = api.projects.list()
             return render_template('config_step2.html', config=config, projects=projects)
        except:
             return redirect(url_for('config_page'))

@app.route('/config/step3', methods=['POST'])
def config_step3():
    config = Config.query.first()

    try:
        is_new_setup = config.setup_time is None

        us_id = int(request.form.get('taiga_user_story_id'))
        us_ref = int(request.form.get('taiga_user_story_ref'))

        config.taiga_user_story_id = us_id
        config.taiga_user_story_ref = us_ref

        if is_new_setup:
            config.setup_time = datetime.now(timezone.utc)

        db.session.commit()

        # Register webhook
        success, message = register_taiga_webhook(config, request.host_url)
        if success:
            flash(f'Configuration saved successfully! {message}', 'success')
        else:
            flash(f'Configuration saved, but webhook failed: {message}', 'warning')

        return redirect(url_for('config_page'))

    except Exception as e:
        flash(f"Error saving configuration: {e}", "error")
        return redirect(url_for('config_page'))


@app.route('/status')
def status_page():
    config = Config.query.first()
    logs = SyncLog.query.order_by(SyncLog.timestamp.desc()).limit(20).all()
    return render_template('status.html', config=config, logs=logs)

@app.route('/taiga-webhook', methods=['POST'])
def taiga_webhook():
    config = Config.query.first()
    if not config or not config.taiga_project_slug:
        return {"status": "ignored", "message": "Taiga project not configured"}, 200

    payload = request.json
    if not payload:
        return {"status": "error", "message": "Invalid JSON"}, 400

    action = payload.get('action')
    obj_type = payload.get('type')

    if obj_type != 'task':
        return {"status": "ignored", "message": "Not a task"}, 200

    data = payload.get('data', {})

    taiga_task_id = data.get('id')
    if not taiga_task_id:
        return {"status": "error", "message": "No task ID in payload"}, 400

    mapping = TaskMapping.query.filter_by(taiga_task_id=taiga_task_id).first()
    if not mapping:
        return {"status": "ignored", "message": "Unmapped task"}, 200

    if action == 'change':
        change = payload.get('change', {})
        diff = change.get('diff', {})

        status_changed = 'status' in diff
        if status_changed:
            is_closed = data.get('is_closed', False)
            if is_closed:
                mark_nextcloud_task_completed(config, mapping.nextcloud_task_uid)

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

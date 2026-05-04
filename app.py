from flask import Flask, render_template, request, redirect, url_for, flash
from database import init_db
from models import db, GlobalConfig, SyncConnection, TaskMapping, SyncLog
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

from sync import mark_nextcloud_task_completed, update_nextcloud_task_details, get_taiga_api, get_caldav_client, connection_status

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.context_processor
def inject_connection_status():
    return {'connection_status': connection_status}

@app.route('/')
def index():
    return redirect(url_for('config_page'))

@app.route('/config', methods=['GET'])
def config_page():
    config = GlobalConfig.query.first()
    connections = SyncConnection.query.all()
    return render_template('config.html', config=config, connections=connections)

@app.route('/config/save_global', methods=['POST'])
def save_global_config():
    config = GlobalConfig.query.first()
    if not config:
        config = GlobalConfig()
        db.session.add(config)

    config.nextcloud_url = request.form['nextcloud_url']
    config.nextcloud_username = request.form['nextcloud_username']
    config.nextcloud_app_password = request.form['nextcloud_app_password']

    config.taiga_url = request.form['taiga_url']
    config.taiga_username = request.form['taiga_username']
    config.taiga_password = request.form['taiga_password']

    db.session.commit()
    flash("Global credentials saved successfully.", "success")
    return redirect(url_for('config_page'))

@app.route('/connection/new/step1', methods=['GET'])
def new_connection_step1():
    config = GlobalConfig.query.first()
    if not config or not config.nextcloud_url or not config.taiga_url:
        flash("Please save global credentials first.", "error")
        return redirect(url_for('config_page'))

    try:
        api = get_taiga_api(config)
        projects = api.projects.list()

        client = get_caldav_client(config)
        principal = client.principal()
        calendars = principal.calendars()

        cal_data = []
        for cal in calendars:
            try:
                supported = cal.get_supported_components()
                if 'VTODO' in supported:
                    cal_data.append({'id': str(cal.url), 'name': cal.name or str(cal.url)})
            except Exception as e:
                logger.warning(f"Failed to check supported components for {cal.url}: {e}")

        if not cal_data and calendars:
            for cal in calendars:
                cal_data.append({'id': str(cal.url), 'name': cal.name or str(cal.url)})

        return render_template('new_conn_step1.html', projects=projects, calendars=cal_data)
    except Exception as e:
        logger.error(f"Auth Error: {e}")
        flash(f"Failed to authenticate with Nextcloud or Taiga: {e}", "error")
        return redirect(url_for('config_page'))

@app.route('/connection/new/step2', methods=['POST'])
def new_connection_step2():
    config = GlobalConfig.query.first()
    nc_id = request.form.get('nextcloud_task_list_id')
    nc_name = request.form.get('nextcloud_task_list')
    taiga_id = int(request.form.get('taiga_project_id'))
    taiga_slug = request.form.get('taiga_project_slug')

    try:
        api = get_taiga_api(config)
        user_stories = api.user_stories.list(project=taiga_id)
        return render_template('new_conn_step2.html',
                               user_stories=user_stories,
                               nc_id=nc_id, nc_name=nc_name,
                               taiga_id=taiga_id, taiga_slug=taiga_slug)
    except Exception as e:
        flash(f"Error fetching user stories: {e}", "error")
        return redirect(url_for('new_connection_step1'))

@app.route('/connection/new/save', methods=['POST'])
def new_connection_save():
    nc_id = request.form.get('nextcloud_task_list_id')
    nc_name = request.form.get('nextcloud_task_list')
    taiga_id = int(request.form.get('taiga_project_id'))
    taiga_slug = request.form.get('taiga_project_slug')

    us_id_str = request.form.get('taiga_user_story_id')
    us_ref_str = request.form.get('taiga_user_story_ref')

    us_id = int(us_id_str) if us_id_str else None
    us_ref = int(us_ref_str) if us_ref_str else None

    conn = SyncConnection(
        nextcloud_task_list=nc_name,
        nextcloud_task_list_id=nc_id,
        taiga_project_id=taiga_id,
        taiga_project_slug=taiga_slug,
        taiga_user_story_id=us_id,
        taiga_user_story_ref=us_ref
    )
    db.session.add(conn)
    db.session.commit()
    flash("New Sync Connection created successfully!", "success")
    return redirect(url_for('config_page'))

@app.route('/connection/<int:conn_id>/delete', methods=['POST'])
def delete_connection(conn_id):
    conn = SyncConnection.query.get_or_404(conn_id)
    db.session.delete(conn)
    db.session.commit()
    flash("Sync Connection deleted.", "success")
    return redirect(url_for('config_page'))


@app.route('/status')
def status_page():
    config = GlobalConfig.query.first()
    connections = SyncConnection.query.all()
    logs = SyncLog.query.order_by(SyncLog.timestamp.desc()).limit(50).all()
    return render_template('status.html', config=config, connections=connections, logs=logs)

@app.route('/logs/clear', methods=['POST'])
def clear_logs():
    SyncLog.query.delete()
    db.session.commit()
    flash("Sync logs cleared.", "success")
    return redirect(url_for('status_page'))

from apscheduler.schedulers.background import BackgroundScheduler
import sync

def run_sync_job():
    sync.sync_nextcloud_to_taiga(app)

scheduler = BackgroundScheduler()
scheduler.add_job(func=run_sync_job, trigger="interval", seconds=30)
scheduler.start()

if __name__ == '__main__':
    app.run(debug=True, port=5001, host='0.0.0.0', use_reloader=False)

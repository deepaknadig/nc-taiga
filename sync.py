import caldav
from datetime import datetime, timezone
import logging

from models import db, Config, TaskMapping, SyncLog
from taiga import TaigaAPI
import requests

logger = logging.getLogger(__name__)

def log_sync_status(status, message):
    try:
        log = SyncLog(status=status, message=message, timestamp=datetime.now(timezone.utc))
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        logger.error(f"Failed to log sync status: {e}")

def get_caldav_client(config):
    if not config.nextcloud_url or not config.nextcloud_username or not config.nextcloud_app_password:
        raise ValueError("Nextcloud configuration is incomplete.")
    client = caldav.DAVClient(
        url=config.nextcloud_url,
        username=config.nextcloud_username,
        password=config.nextcloud_app_password
    )
    return client

def get_task_list(client, list_name):
    principal = client.principal()
    calendars = principal.calendars()
    for calendar in calendars:
        if calendar.name == list_name:
            return calendar
    raise ValueError(f"Task list '{list_name}' not found.")

def mark_nextcloud_task_completed(config, task_uid):
    try:
        client = get_caldav_client(config)
        calendar = get_task_list(client, config.nextcloud_task_list)

        task_vobject = calendar.todo_by_uid(task_uid)

        if task_vobject:
            vtodo = task_vobject.instance.vtodo
            if hasattr(vtodo, 'status'):
                vtodo.status.value = 'COMPLETED'
            else:
                vtodo.add('status').value = 'COMPLETED'
            # Check if completed date already exists, if not add it
            if not hasattr(vtodo, 'completed'):
                vtodo.add('completed').value = datetime.now(timezone.utc)
            else:
                vtodo.completed.value = datetime.now(timezone.utc)
            task_vobject.save()
            log_sync_status('SUCCESS', f"Marked Nextcloud task {task_uid} as COMPLETED.")
            return True
        else:
            log_sync_status('ERROR', f"Task with UID {task_uid} not found in Nextcloud.")
            return False

    except Exception as e:
        logger.error(f"Error marking task complete in Nextcloud: {e}")
        log_sync_status('ERROR', f"Error marking task complete in Nextcloud: {e}")
        return False

def update_nextcloud_task_details(config, task_uid, title, description):
    try:
        client = get_caldav_client(config)
        calendar = get_task_list(client, config.nextcloud_task_list)

        task_vobject = calendar.todo_by_uid(task_uid)

        if task_vobject:
            vtodo = task_vobject.instance.vtodo

            if title:
                if hasattr(vtodo, 'summary'):
                    vtodo.summary.value = title
                else:
                    vtodo.add('summary').value = title

            if description is not None:
                if hasattr(vtodo, 'description'):
                    vtodo.description.value = description
                else:
                    if description:
                        vtodo.add('description').value = description

            task_vobject.save()
            log_sync_status('SUCCESS', f"Updated Nextcloud task {task_uid} details.")
            return True
        else:
            log_sync_status('ERROR', f"Task with UID {task_uid} not found in Nextcloud.")
            return False

    except Exception as e:
        logger.error(f"Error updating task in Nextcloud: {e}")
        log_sync_status('ERROR', f"Error updating task in Nextcloud: {e}")
        return False

def get_taiga_api(config):
    if not config.taiga_url or not config.taiga_username or not config.taiga_password:
        raise ValueError("Taiga configuration is incomplete.")

    api = TaigaAPI(host=config.taiga_url)
    api.auth(username=config.taiga_username, password=config.taiga_password)
    return api

def sync_nextcloud_to_taiga(app):
    with app.app_context():
        try:
            config = Config.query.first()
            if not config or not config.nextcloud_url or not config.taiga_project_id:
                return # Not fully configured yet

            try:
                client = get_caldav_client(config)
                calendar = get_task_list(client, config.nextcloud_task_list)
            except Exception as e:
                log_sync_status('ERROR', f"Failed to connect to Nextcloud: {e}")
                return

            try:
                taiga_api = get_taiga_api(config)
                project = taiga_api.projects.get(config.taiga_project_id)
            except Exception as e:
                logger.error(f"Failed to find Taiga project ID {config.taiga_project_id}: {e}")
                log_sync_status('ERROR', f"Taiga project ID {config.taiga_project_id} not found.")
                return

            user_story = None
            if config.taiga_user_story_id:
                try:
                    user_story = taiga_api.user_stories.get(config.taiga_user_story_id)
                except Exception as e:
                    logger.error(f"Failed to find User Story ID {config.taiga_user_story_id}: {e}")
                    log_sync_status('ERROR', f"Taiga User Story ID {config.taiga_user_story_id} not found.")
                    return

            try:
                nextcloud_tasks = calendar.todos()
            except Exception as e:
                log_sync_status('ERROR', f"Failed to fetch tasks from Nextcloud: {e}")
                return

            for nc_task in nextcloud_tasks:
                try:
                    vtodo = nc_task.instance.vtodo
                    uid = vtodo.uid.value

                    mapping = TaskMapping.query.filter_by(nextcloud_task_uid=uid).first()

                    title = vtodo.summary.value if hasattr(vtodo, 'summary') else "Untitled Task"
                    description = vtodo.description.value if hasattr(vtodo, 'description') else ""

                    is_completed = False
                    if hasattr(vtodo, 'status') and vtodo.status.value == 'COMPLETED':
                        is_completed = True
                    if hasattr(vtodo, 'completed'):
                        is_completed = True

                    if not mapping:
                        created_dt = None
                        if hasattr(vtodo, 'created'):
                            created_dt = vtodo.created.value
                        elif hasattr(vtodo, 'dtstamp'):
                            created_dt = vtodo.dtstamp.value

                        # Ensure created_dt is timezone aware
                        if created_dt and created_dt.tzinfo is None:
                            created_dt = created_dt.replace(tzinfo=timezone.utc)

                        config_setup_time = config.setup_time
                        if config_setup_time and config_setup_time.tzinfo is None:
                            config_setup_time = config_setup_time.replace(tzinfo=timezone.utc)

                        # If setup_time is not set, we skip creating new tasks.
                        if not config_setup_time:
                            continue

                        if created_dt and config_setup_time and created_dt < config_setup_time:
                           continue # It's an old task created before the app was configured

                        if is_completed:
                           continue

                        logger.info(f"Creating new Taiga task for Nextcloud task {uid}")
                        task_data = {
                            "project": project.id,
                            "subject": title,
                            "description": description,
                        }
                        if user_story:
                            task_data["user_story"] = user_story.id

                        new_taiga_task = taiga_api.tasks.create(project=project.id, subject=title, description=description, user_story=user_story.id if user_story else None)

                        new_mapping = TaskMapping(nextcloud_task_uid=uid, taiga_task_id=new_taiga_task.id)
                        db.session.add(new_mapping)
                        db.session.commit()

                        log_sync_status('SUCCESS', f"Synced new task '{title}' to Taiga.")

                    else:
                        try:
                            t_task = taiga_api.tasks.get(mapping.taiga_task_id)

                            updated = False
                            if t_task.subject != title or t_task.description != description:
                                t_task.subject = title
                                t_task.description = description
                                t_task.update()
                                updated = True

                            if is_completed and not t_task.is_closed:
                                closed_status = None
                                for status in project.task_statuses:
                                    if status.is_closed:
                                        closed_status = status.id
                                        break

                                if closed_status:
                                    t_task.status = closed_status
                                    t_task.update()
                                    updated = True

                            if updated:
                                log_sync_status('SUCCESS', f"Updated mapped task '{title}' in Taiga.")

                        except Exception as e:
                             logger.error(f"Error updating existing Taiga task {mapping.taiga_task_id}: {e}")

                except Exception as e:
                    logger.error(f"Error processing Nextcloud task: {e}")
                    log_sync_status('ERROR', f"Error processing a Nextcloud task: {e}")

            config.last_sync_time = datetime.now(timezone.utc)
            db.session.commit()

        except Exception as e:
            logger.error(f"General sync error: {e}")
            log_sync_status('ERROR', f"General sync error: {e}")

def register_taiga_webhook(config, public_url):
    try:
        taiga_api = get_taiga_api(config)
        project_id = config.taiga_project_id

        # We need the full URL to the webhook endpoint
        webhook_url = f"{public_url.rstrip('/')}/taiga-webhook"

        # When using api.auth(username, password), taiga_api.token is set
        headers = {
            "Authorization": f"Bearer {taiga_api.token}",
            "Content-Type": "application/json"
        }

        # Check existing webhooks for this project
        taiga_base = config.taiga_url.rstrip('/')
        if not taiga_base.endswith('/api/v1'):
            taiga_api_base = f"{taiga_base}/api/v1"
        else:
            taiga_api_base = taiga_base

        # Add a timeout so it does not hang if Taiga API is unresponsive
        res = requests.get(f"{taiga_api_base}/webhooks?project={project_id}", headers=headers, timeout=10)

        if res.status_code == 200:
            webhooks = res.json()
            for wh in webhooks:
                if wh.get('url') == webhook_url or wh.get('name') == "Nextcloud Task Sync":
                    del_res = requests.delete(f"{taiga_api_base}/webhooks/{wh['id']}", headers=headers, timeout=10)
                    if del_res.status_code not in [200, 204]:
                        logger.warning(f"Failed to delete existing Taiga webhook {wh['id']}: {del_res.text}")

        # Create new webhook
        payload = {
            "project": project_id,
            "url": webhook_url,
            "name": "Nextcloud Task Sync",
            "key": "nc-taiga-sync-secret"
        }
        create_res = requests.post(f"{taiga_api_base}/webhooks", json=payload, headers=headers, timeout=10)

        if create_res.status_code in [201, 200]:
            logger.info(f"Successfully registered Taiga webhook at {webhook_url}")
            return True, "Webhook successfully registered in Taiga."
        else:
            logger.error(f"Failed to register Taiga webhook: {create_res.text}")
            return False, f"Failed to register webhook in Taiga API: {create_res.text}"

    except Exception as e:
        logger.error(f"Error registering Taiga webhook: {e}")
        return False, f"Error registering webhook: {str(e)}"

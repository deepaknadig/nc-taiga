import caldav
from datetime import datetime, timezone
import logging

from models import db, Config, TaskMapping, SyncLog
from taiga import TaigaAPI

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
    if not config.taiga_url or not config.taiga_token:
        raise ValueError("Taiga configuration is incomplete.")

    api = TaigaAPI(host=config.taiga_url)
    api.token = config.taiga_token
    return api

def sync_nextcloud_to_taiga(app):
    with app.app_context():
        try:
            config = Config.query.first()
            if not config or not config.nextcloud_url or not config.taiga_project_slug:
                return # Not fully configured yet

            try:
                client = get_caldav_client(config)
                calendar = get_task_list(client, config.nextcloud_task_list)
            except Exception as e:
                log_sync_status('ERROR', f"Failed to connect to Nextcloud: {e}")
                return

            try:
                taiga_api = get_taiga_api(config)
                project = taiga_api.projects.get_by_slug(config.taiga_project_slug)
            except Exception as e:
                logger.error(f"Failed to find Taiga project {config.taiga_project_slug}: {e}")
                log_sync_status('ERROR', f"Taiga project {config.taiga_project_slug} not found.")
                return

            user_story = None
            if config.taiga_user_story_ref:
                try:
                    user_story = taiga_api.user_stories.get_by_ref(project.id, config.taiga_user_story_ref)
                except Exception as e:
                    logger.error(f"Failed to find User Story by ref {config.taiga_user_story_ref}: {e}")
                    log_sync_status('ERROR', f"Taiga User Story ref {config.taiga_user_story_ref} not found.")
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

                        new_taiga_task = taiga_api.tasks.create(**task_data)

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

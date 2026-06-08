import caldav
from datetime import datetime, timezone
import pytz
import logging
import time

from models import db, GlobalConfig, SyncConnection, TaskMapping, SyncLog
from taiga import TaigaAPI
import requests

logger = logging.getLogger(__name__)

CONNECT_MAX_RETRIES = 3
CONNECT_RETRY_DELAY = 5  # seconds between retries

# Tracks the last connectivity check for Nextcloud and Taiga.
# Updated at the start of every sync cycle.
connection_status = {
    'nextcloud': {'ok': None, 'last_checked': None, 'error': None, 'label': 'Unknown', 'retries': 0},
    'taiga':     {'ok': None, 'last_checked': None, 'error': None, 'label': 'Unknown', 'retries': 0},
}

def _connect_with_retry(connect_fn, service_name, max_retries=CONNECT_MAX_RETRIES, retry_delay=CONNECT_RETRY_DELAY):
    """
    Call connect_fn() up to max_retries times with retry_delay seconds between attempts.
    Logs each retry and the final outcome. Returns (result, retries_used) on success,
    or raises the last exception after all attempts are exhausted.
    """
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            result = connect_fn()
            if attempt > 1:
                msg = f"{service_name}: connection re-established after {attempt - 1} retry(ies)."
                logger.info(msg)
                log_sync_status('SUCCESS', msg)
            else:
                logger.info(f"{service_name}: connected successfully.")
            return result, attempt - 1
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                msg = (
                    f"{service_name}: connection attempt {attempt}/{max_retries} failed — {e}. "
                    f"Retrying in {retry_delay}s..."
                )
                logger.warning(msg)
                log_sync_status('ERROR', msg)
                time.sleep(retry_delay)
            else:
                msg = f"{service_name}: unavailable after {max_retries} attempt(s) — {e}."
                logger.error(msg)
                log_sync_status('ERROR', msg)
    raise last_error

def log_sync_status(status, message, connection_id=None):
    try:
        log = SyncLog(status=status, message=message, timestamp=datetime.now(timezone.utc), connection_id=connection_id)
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

def get_task_list(client, connection):
    principal = client.principal()
    calendars = principal.calendars()

    target_id = connection.nextcloud_task_list_id
    target_name = connection.nextcloud_task_list

    for calendar in calendars:
        if target_id and str(calendar.url) == target_id:
            return calendar

    for calendar in calendars:
        if calendar.name == target_name or str(calendar.url) == target_name:
            return calendar

    raise ValueError(f"Task list '{target_name}' not found.")

def mark_nextcloud_task_completed(config, connection, task_uid):
    try:
        client = get_caldav_client(config)
        calendar = get_task_list(client, connection)

        task_vobject = calendar.todo_by_uid(task_uid)

        if task_vobject:
            vtodo = task_vobject.instance.vtodo

            if hasattr(vtodo, 'status') and vtodo.status.value == 'COMPLETED':
                return True

            if hasattr(vtodo, 'status'):
                vtodo.status.value = 'COMPLETED'
            else:
                vtodo.add('status').value = 'COMPLETED'
            # To fix vobject's `Unable to guess TZID for tzinfo UTC` error,
            # we must use a strictly timezone-aware pytz object like this:
            completed_dt = datetime.now(pytz.utc)

            if not hasattr(vtodo, 'completed'):
                vtodo.add('completed').value = completed_dt
            else:
                vtodo.completed.value = completed_dt

            task_vobject.save()
            log_sync_status('SUCCESS', f"Marked Nextcloud task {task_uid} as COMPLETED.", connection_id=connection.id)
            return True
        else:
            log_sync_status('ERROR', f"Task with UID {task_uid} not found in Nextcloud.", connection_id=connection.id)
            return False

    except Exception as e:
        logger.error(f"Error marking task complete in Nextcloud: {e}")
        log_sync_status('ERROR', f"Error marking task complete in Nextcloud: {e}", connection_id=connection.id)
        return False

def update_nextcloud_task_details(config, connection, task_uid, title, description):
    try:
        client = get_caldav_client(config)
        calendar = get_task_list(client, connection)

        task_vobject = calendar.todo_by_uid(task_uid)

        if task_vobject:
            vtodo = task_vobject.instance.vtodo

            changed = False

            if title:
                if hasattr(vtodo, 'summary'):
                    if vtodo.summary.value != title:
                        vtodo.summary.value = title
                        changed = True
                else:
                    vtodo.add('summary').value = title
                    changed = True

            if description is not None:
                if hasattr(vtodo, 'description'):
                    if vtodo.description.value != description:
                        vtodo.description.value = description
                        changed = True
                else:
                    if description:
                        vtodo.add('description').value = description
                        changed = True

            if changed:
                task_vobject.save()
                log_sync_status('SUCCESS', f"Updated Nextcloud task {task_uid} details.", connection_id=connection.id)
            return True
        else:
            log_sync_status('ERROR', f"Task with UID {task_uid} not found in Nextcloud.", connection_id=connection.id)
            return False

    except Exception as e:
        logger.error(f"Error updating task in Nextcloud: {e}")
        log_sync_status('ERROR', f"Error updating task in Nextcloud: {e}", connection_id=connection.id)
        return False

def delete_nextcloud_task(config, connection, task_uid):
    """Delete a VTODO from Nextcloud by UID. Raises on failure."""
    client = get_caldav_client(config)
    calendar = get_task_list(client, connection)
    task_vobject = calendar.todo_by_uid(task_uid)
    if task_vobject:
        task_vobject.delete()

def get_taiga_api(config):
    if not config.taiga_url or not config.taiga_username or not config.taiga_password:
        raise ValueError("Taiga configuration is incomplete.")

    api = TaigaAPI(host=config.taiga_url)
    api.auth(username=config.taiga_username, password=config.taiga_password)
    return api

def sync_nextcloud_to_taiga(app):
    with app.app_context():
        try:
            config = GlobalConfig.query.first()
            if not config or not config.nextcloud_url:
                return

            connections = SyncConnection.query.all()
            if not connections:
                return

            now = datetime.now(timezone.utc)

            # Test Nextcloud connectivity (with retries)
            try:
                def _nc_connect():
                    c = get_caldav_client(config)
                    c.principal()
                    return c
                client, nc_retries = _connect_with_retry(_nc_connect, 'Nextcloud')
                nc_label = 'Connected' if nc_retries == 0 else f'Reconnected (after {nc_retries} {"retry" if nc_retries == 1 else "retries"})'
                connection_status['nextcloud'].update({
                    'ok': True, 'last_checked': now, 'error': None,
                    'label': nc_label, 'retries': nc_retries,
                })
            except Exception as e:
                connection_status['nextcloud'].update({
                    'ok': False, 'last_checked': now, 'error': str(e),
                    'label': 'Unavailable', 'retries': CONNECT_MAX_RETRIES,
                })
                return

            # Test Taiga connectivity (with retries)
            try:
                taiga_api, tg_retries = _connect_with_retry(lambda: get_taiga_api(config), 'Taiga')
                tg_label = 'Connected' if tg_retries == 0 else f'Reconnected (after {tg_retries} {"retry" if tg_retries == 1 else "retries"})'
                connection_status['taiga'].update({
                    'ok': True, 'last_checked': now, 'error': None,
                    'label': tg_label, 'retries': tg_retries,
                })
            except Exception as e:
                connection_status['taiga'].update({
                    'ok': False, 'last_checked': now, 'error': str(e),
                    'label': 'Unavailable', 'retries': CONNECT_MAX_RETRIES,
                })
                return

            for connection in connections:
                try:
                    calendar = get_task_list(client, connection)
                except Exception as e:
                    log_sync_status('ERROR', f"Failed to connect to Nextcloud calendar: {e}", connection_id=connection.id)
                    continue

                try:
                    project = taiga_api.projects.get(connection.taiga_project_id)
                except Exception as e:
                    logger.error(f"Failed to find Taiga project ID {connection.taiga_project_id}: {e}")
                    log_sync_status('ERROR', f"Taiga project ID {connection.taiga_project_id} not found.", connection_id=connection.id)
                    continue

                user_story = None
                if connection.taiga_user_story_id:
                    try:
                        user_story = taiga_api.user_stories.get(connection.taiga_user_story_id)
                    except Exception as e:
                        logger.error(f"Failed to find User Story ID {connection.taiga_user_story_id}: {e}")
                        log_sync_status('ERROR', f"Taiga User Story ID {connection.taiga_user_story_id} not found.", connection_id=connection.id)
                        continue

                try:
                    nextcloud_tasks = calendar.todos()
                except Exception as e:
                    log_sync_status('ERROR', f"Failed to fetch tasks from Nextcloud: {e}", connection_id=connection.id)
                    continue

                try:
                    if user_story:
                        existing_taiga_tasks = taiga_api.tasks.list(user_story=user_story.id)
                    else:
                        existing_taiga_tasks = taiga_api.tasks.list(project=project.id)
                except Exception as e:
                    logger.error(f"Failed to pre-fetch Taiga tasks for deduplication: {e}")
                    existing_taiga_tasks = []

                # ── Phase 1: Nextcloud → Taiga (new tasks and updates) ──────────────
                for nc_task in nextcloud_tasks:
                    try:
                        vtodo = nc_task.instance.vtodo
                        uid = vtodo.uid.value

                        mapping = TaskMapping.query.filter_by(connection_id=connection.id, nextcloud_task_uid=uid).first()

                        title = vtodo.summary.value if hasattr(vtodo, 'summary') else "Untitled Task"
                        description = vtodo.description.value if hasattr(vtodo, 'description') else ""

                        is_completed = False
                        if hasattr(vtodo, 'status') and vtodo.status.value == 'COMPLETED':
                            is_completed = True
                        if hasattr(vtodo, 'completed'):
                            is_completed = True

                        if not mapping:
                            matching_taiga_task = None
                            for t_task in existing_taiga_tasks:
                                if t_task.subject == title:
                                    matching_taiga_task = t_task
                                    break

                            if matching_taiga_task:
                                logger.info(f"Found existing Taiga task '{title}' matching Nextcloud task {uid}. Creating mapping instead of duplicating.")
                                new_mapping = TaskMapping(
                                    connection_id=connection.id,
                                    nextcloud_task_uid=uid,
                                    taiga_task_id=matching_taiga_task.id,
                                    last_known_taiga_subject=matching_taiga_task.subject,
                                    last_known_taiga_status=matching_taiga_task.is_closed
                                )
                                try:
                                    full_t_task = taiga_api.tasks.get(matching_taiga_task.id)
                                    new_mapping.last_known_taiga_description = full_t_task.description
                                except Exception as e:
                                    logger.warning(f"Could not fetch full description for matched task {matching_taiga_task.id}: {e}")
                                    new_mapping.last_known_taiga_description = ""

                                db.session.add(new_mapping)
                                db.session.commit()
                                log_sync_status('SUCCESS', f"Mapped existing Taiga task '{title}' to Nextcloud.", connection_id=connection.id)
                                continue

                            logger.info(f"Creating new Taiga task for Nextcloud task {uid}")

                            new_status_id = None
                            if hasattr(project, 'task_statuses') and project.task_statuses:
                                if is_completed:
                                    for status in project.task_statuses:
                                        if status.is_closed:
                                            new_status_id = status.id
                                            break
                                else:
                                    for status in project.task_statuses:
                                        if not status.is_closed:
                                            new_status_id = status.id
                                            break

                                if not new_status_id:
                                    new_status_id = project.task_statuses[0].id

                            if not new_status_id:
                                logger.error("Could not find a valid task status in the Taiga project to create the task.")
                                continue

                            task_data = {
                                "project": project.id,
                                "subject": title,
                                "description": description,
                                "status": new_status_id
                            }
                            if user_story:
                                task_data["user_story"] = user_story.id

                            new_taiga_task = taiga_api.tasks.create(**task_data)

                            new_mapping = TaskMapping(
                                connection_id=connection.id,
                                nextcloud_task_uid=uid,
                                taiga_task_id=new_taiga_task.id,
                                last_known_taiga_subject=new_taiga_task.subject,
                                last_known_taiga_description=new_taiga_task.description,
                                last_known_taiga_status=new_taiga_task.is_closed
                            )
                            db.session.add(new_mapping)
                            db.session.commit()

                            log_sync_status('SUCCESS', f"Synced new task '{title}' to Taiga.", connection_id=connection.id)

                        else:
                            # Sync Nextcloud → Taiga updates and completions
                            try:
                                t_task = taiga_api.tasks.get(mapping.taiga_task_id)

                                updated = False

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
                                        mapping.last_known_taiga_status = True
                                        db.session.commit()

                                if title != mapping.last_known_taiga_subject or description != mapping.last_known_taiga_description:
                                    # Nextcloud fields differ from what we last saw in Taiga — assume NC was edited.
                                    t_task.subject = title
                                    t_task.description = description
                                    t_task.update()
                                    updated = True
                                    mapping.last_known_taiga_subject = title
                                    mapping.last_known_taiga_description = description
                                    db.session.commit()

                                if updated:
                                    log_sync_status('SUCCESS', f"Updated mapped task '{title}' in Taiga.", connection_id=connection.id)

                            except Exception as e:
                                logger.error(f"Error updating existing Taiga task {mapping.taiga_task_id}: {e}")

                    except Exception as e:
                        logger.error(f"Error processing Nextcloud task: {e}")
                        log_sync_status('ERROR', f"Error processing a Nextcloud task: {e}", connection_id=connection.id)

                # ── Deletion: NC task removed → delete from Taiga ────────────────────
                nc_uids = {nc_task.instance.vtodo.uid.value for nc_task in nextcloud_tasks}
                stale_mappings = [
                    m for m in TaskMapping.query.filter_by(connection_id=connection.id).all()
                    if m.nextcloud_task_uid not in nc_uids
                ]
                for mapping in stale_mappings:
                    try:
                        taiga_api.tasks.delete(mapping.taiga_task_id)
                        log_sync_status('SUCCESS', f"Deleted Taiga task #{mapping.taiga_task_id} because its Nextcloud task was removed.", connection_id=connection.id)
                    except Exception as e:
                        log_sync_status('ERROR', f"Failed to delete Taiga task #{mapping.taiga_task_id} after NC removal: {e}", connection_id=connection.id)
                    db.session.delete(mapping)
                    db.session.commit()

                connection.last_sync_time = datetime.now(timezone.utc)
                db.session.commit()

                # ── Phase 2: Taiga → Nextcloud (new tasks and updates) ───────────────
                try:
                    if user_story:
                        taiga_tasks = taiga_api.tasks.list(user_story=user_story.id)
                    else:
                        taiga_tasks = taiga_api.tasks.list(project=project.id)

                    taiga_task_ids = {t.id for t in taiga_tasks}

                    for t_task_summary in taiga_tasks:
                        mapping = TaskMapping.query.filter_by(connection_id=connection.id, taiga_task_id=t_task_summary.id).first()
                        if mapping:
                            t_task = taiga_api.tasks.get(t_task_summary.id)

                            changed = False

                            if t_task.is_closed and not mapping.last_known_taiga_status:
                                if mark_nextcloud_task_completed(config, connection, mapping.nextcloud_task_uid):
                                    mapping.last_known_taiga_status = True
                                    changed = True

                            if t_task.subject != mapping.last_known_taiga_subject or t_task.description != mapping.last_known_taiga_description:
                                if update_nextcloud_task_details(config, connection, mapping.nextcloud_task_uid, t_task.subject, t_task.description):
                                    mapping.last_known_taiga_subject = t_task.subject
                                    mapping.last_known_taiga_description = t_task.description
                                    changed = True

                            if changed:
                                db.session.commit()
                        else:
                            t_task = taiga_api.tasks.get(t_task_summary.id)

                            matching_nc_task = None
                            for nc_t in nextcloud_tasks:
                                nc_vtodo = nc_t.instance.vtodo
                                if hasattr(nc_vtodo, 'summary') and nc_vtodo.summary.value == t_task.subject:
                                    if not TaskMapping.query.filter_by(connection_id=connection.id, nextcloud_task_uid=nc_vtodo.uid.value).first():
                                        matching_nc_task = nc_vtodo
                                        break

                            if matching_nc_task:
                                logger.info(f"Found existing Nextcloud task matching Taiga task '{t_task.subject}'. Mapping instead of creating.")
                                new_mapping = TaskMapping(
                                    connection_id=connection.id,
                                    nextcloud_task_uid=matching_nc_task.uid.value,
                                    taiga_task_id=t_task.id,
                                    last_known_taiga_subject=t_task.subject,
                                    last_known_taiga_description=t_task.description,
                                    last_known_taiga_status=t_task.is_closed
                                )
                                db.session.add(new_mapping)
                                db.session.commit()
                                log_sync_status('SUCCESS', f"Mapped existing Nextcloud task '{t_task.subject}' to Taiga.", connection_id=connection.id)
                                continue

                            logger.info(f"Creating new Nextcloud task for Taiga task '{t_task.subject}'")
                            try:
                                import vobject
                                import uuid

                                v = vobject.iCalendar()
                                v.add('vtodo')
                                v.vtodo.add('summary').value = t_task.subject
                                v.vtodo.add('description').value = t_task.description or ""

                                new_uid = str(uuid.uuid4())
                                v.vtodo.add('uid').value = new_uid
                                v.vtodo.add('dtstamp').value = datetime.now(pytz.utc)

                                ical_str = v.serialize()
                                calendar.save_todo(ical=ical_str)

                                new_mapping = TaskMapping(
                                    connection_id=connection.id,
                                    nextcloud_task_uid=new_uid,
                                    taiga_task_id=t_task.id,
                                    last_known_taiga_subject=t_task.subject,
                                    last_known_taiga_description=t_task.description,
                                    last_known_taiga_status=t_task.is_closed
                                )
                                db.session.add(new_mapping)
                                db.session.commit()

                                if t_task.is_closed:
                                    mark_nextcloud_task_completed(config, connection, new_uid)

                                log_sync_status('SUCCESS', f"Synced new Taiga task '{t_task.subject}' to Nextcloud.", connection_id=connection.id)
                            except Exception as e:
                                logger.error(f"Error creating Nextcloud task from Taiga: {e}")
                                log_sync_status('ERROR', f"Error creating Nextcloud task from Taiga: {e}", connection_id=connection.id)

                    # ── Deletion: Taiga task removed → delete from NC ─────────────────
                    stale_mappings = [
                        m for m in TaskMapping.query.filter_by(connection_id=connection.id).all()
                        if m.taiga_task_id not in taiga_task_ids
                    ]
                    for mapping in stale_mappings:
                        try:
                            delete_nextcloud_task(config, connection, mapping.nextcloud_task_uid)
                            log_sync_status('SUCCESS', f"Deleted NC task '{mapping.nextcloud_task_uid}' because its Taiga task was removed.", connection_id=connection.id)
                        except Exception as e:
                            log_sync_status('ERROR', f"Failed to delete NC task '{mapping.nextcloud_task_uid}' after Taiga removal: {e}", connection_id=connection.id)
                        db.session.delete(mapping)
                        db.session.commit()

                except Exception as e:
                    logger.error(f"Error fetching tasks from Taiga: {e}")
                    log_sync_status('ERROR', f"Error fetching tasks from Taiga: {e}", connection_id=connection.id)

        except Exception as e:
            logger.error(f"General sync error: {e}")
            log_sync_status('ERROR', f"General sync error: {e}")

# Nextcloud-Taiga Task Sync Web App

A Python Flask web application that integrates a Nextcloud Task List (CalDAV) with a Taiga project, keeping tasks in sync bidirectionally without requiring webhooks.

## Features

- **Configuration UI** — Set credentials for Nextcloud and Taiga, and map a Nextcloud Task List to a Taiga Project (optionally scoped to a specific User Story).
- **Bidirectional Sync** — A background job polls every 30 seconds:
  - *Nextcloud → Taiga:* New tasks, title/description edits, and completions are pushed to Taiga.
  - *Taiga → Nextcloud:* New tasks, title/description edits, and completions are pushed to Nextcloud.
  - *Deletion sync:* A task deleted on either side is removed from the other side automatically.
- **Connection Retry** — On startup of each sync cycle, both Nextcloud and Taiga connections are tested with up to 3 retries (5 s apart). Each attempt is logged. Credentials are re-read from the database on every retry, so updating credentials in the UI takes effect immediately — even mid-retry.
- **Live Status Badges** — Every page shows coloured pill badges for `Nextcloud: Connected / Unavailable` and `Taiga: Connected / Unavailable`, updated each sync cycle. Hovering shows the last-checked timestamp and any error detail.
- **Sync Timing Badge** — A blue `↻ Xs ago · Ys` badge shows time since the last completed sync cycle and counts down to the next automatic page refresh.
- **Auto Page Refresh** — The UI refreshes itself every 30 seconds (matching the sync interval) so the status badges and log table stay current without manual reloads.
- **Sync Status Log** — The `/status` page shows the last 50 log entries with a one-click **Clear Logs** button.
- **Health Check Endpoint** — `GET /healthz` returns `{"status": "ok"}`. Used by Docker for container health monitoring.

## Requirements

- Python 3.12+
- A self-hosted Nextcloud instance with the Tasks app installed.
- A self-hosted Taiga instance with API access.

## Installation & Running

### Using Docker Compose (Recommended)

1. Clone this repository.
2. Open `docker-compose.yml` and configure your Nextcloud and Taiga credentials in the `environment` section, or leave them blank and enter them through the UI after startup.
3. Build and start the container:

   ```bash
   docker compose up -d --build
   ```

4. Open `http://localhost:5001/config` in your browser.

The container uses a Gunicorn WSGI server (not the Flask dev server) for stability. A Docker `HEALTHCHECK` pings `/healthz` every 30 s; the container is marked healthy once the app is ready.

The `./instance/` directory is mounted as a volume and holds the SQLite database, so your credentials and sync mappings survive container restarts and rebuilds.

### Running Locally (Without Docker)

1. Clone this repository.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Start the app:

   ```bash
   python app.py
   ```

4. Open `http://localhost:5001/config`.

## Configuration

### Nextcloud

| Field | Description |
|---|---|
| CalDAV URL | `https://your-nextcloud-domain/remote.php/dav/` |
| User ID | Your Nextcloud username |
| App Password | Create one under **Settings → Security → App passwords**. Do not use your main password. |

### Taiga

| Field | Description |
|---|---|
| API URL | `https://api.taiga.io` or your self-hosted URL |
| Username / Email | Your Taiga login |
| Password | Your Taiga password |

### Adding a Sync Connection

1. Save your credentials on the **Configuration** page.
2. Click **Add New Sync Connection**.
3. **Step 1** — Choose the Nextcloud Task List and Taiga Project.
4. **Step 2** — Optionally scope the sync to a specific Taiga User Story, or leave blank to sync all tasks in the project.

## Sync Behaviour

- The background job runs every **30 seconds**.
- On the first run, existing tasks on both sides are matched by title to avoid duplicates.
- Title, description, and completion status are kept in sync bidirectionally.
- Deleting a task on one side removes the corresponding task on the other side on the next sync cycle.
- The sync uses polling only — no webhooks are required. This avoids IP routing issues common with self-hosted instances on the same server.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Badges show `Unavailable` | Wrong credentials or service unreachable | Check URL and credentials on the Configuration page; the retry logic will recover automatically when the service comes back |
| Tasks not syncing | No Sync Connection configured | Add one via **Add New Sync Connection** |
| Duplicate tasks after DB reset | First-run deduplication by title | Clear the extra tasks manually; subsequent syncs will not re-create them |
| Container unhealthy | App not yet ready | Wait for the 15 s start grace period to pass |

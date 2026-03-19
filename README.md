# Nextcloud-Taiga Task Sync Web App

This is a Python Flask web application designed to integrate Tasks from a user-chosen Task List from a self-hosted Nextcloud instance with a self-hosted Taiga instance.

## Features

- **Configuration UI:** Provide configuration interfaces with URLs, user IDs, and app passwords for both Nextcloud and Taiga. Also allows specifying the Taiga Project and User Story (US) ID.
- **Sync Status Dashboard:** A separate web screen to check sync status and review detailed logs.
- **Bi-directional Sync:** A background polling process runs every 30 seconds to fetch tasks from Nextcloud CalDAV and Taiga.
  - *Nextcloud to Taiga:* New tasks created in Nextcloud (after configuration is saved) are synced and attached to the configured Taiga Project and US.
  - *Taiga to Nextcloud:* When tasks are updated or closed in the Taiga Sprint Taskboard for the configured US, the title, description, and completion statuses are marked accordingly in Nextcloud.

## Requirements

- Python 3.12+
- A self-hosted Nextcloud instance with the Tasks app installed.
- A self-hosted Taiga instance with API access.

## Installation

1. Clone this repository.
2. Install the required Python packages using `pip`:

   ```bash
   pip install -r requirements.txt
   ```

## Running the Application

1. Start the Flask application by running:

   ```bash
   python app.py
   ```

2. Open your web browser and navigate to `http://localhost:5001/config` to configure your Nextcloud and Taiga connection details.

## Configuration Details

### Nextcloud Setup

1. **Nextcloud CalDAV URL:** Usually formatted as `https://your-nextcloud-domain/remote.php/dav/`.
2. **Nextcloud User ID:** Your username.
3. **Nextcloud App Password:** Create a new App Password under Nextcloud Settings -> Security. Do not use your primary account password.
4. **Nextcloud Task List Name:** The exact name of the Task List (e.g., `Personal`).

### Taiga Setup

1. **Taiga API URL:** Usually formatted as `https://api.taiga.io` or your self-hosted URL.
2. **Taiga Token / Credentials:** The Taiga API requires authentication. In many self-hosted Taiga instances, "Application Tokens" are managed by site administrators via the Django Admin panel. If you don't have an Application Token, you can also authenticate the app using your standard **Username and Password** or a standard **Auth Token**.
   * *Note: The current app uses the `python-taiga` library. If using a standard username/password instead of a token, you may need to update the `get_taiga_api` function in `sync.py` to use `api.auth(username, password)` instead of `api.token`.*
3. **Taiga Project Slug:** The slug found in your Taiga project's URL (e.g., `myusername-myprojectname`).
4. **Taiga User Story Ref:** The integer ID of the User Story you want tasks assigned to (e.g., `12` for US #12).

### Sync Mechanism

The application utilizes background polling to communicate between Nextcloud and Taiga. It does **not** rely on Taiga Webhooks, completely bypassing local IP restriction errors common with self-hosted instances on the same server.

The background job runs every 30 seconds to check for new tasks in Nextcloud and modified attributes (title, description, and completion status) for existing tasks in Taiga.

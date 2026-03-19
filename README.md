# Nextcloud-Taiga Task Sync Web App

This is a Python Flask web application designed to integrate Tasks from a user-chosen Task List from a self-hosted Nextcloud instance with a self-hosted Taiga instance.

## Features

- **Configuration UI:** Provide configuration interfaces with URLs, user IDs, and app passwords for both Nextcloud and Taiga. Also allows specifying the Taiga Project and User Story (US) ID.
- **Sync Status Dashboard:** A separate web screen to check sync status and review detailed logs.
- **Nextcloud to Taiga Sync:** Background polling of Nextcloud CalDAV (runs every 30 seconds). Whenever a new Task is created in Nextcloud (after configuration is saved), the Task is synced and attached to the configured Taiga Project and US.
- **Taiga to Nextcloud Sync:** Via webhooks. When tasks are updated or closed in the Taiga Sprint Taskboard for a particular US, the title, description, and completion statuses are marked accordingly in Nextcloud.

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

2. Open your web browser and navigate to `http://localhost:5000/config` to configure your Nextcloud and Taiga connection details.

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

### Webhook Configuration

To sync changes *from* Taiga *back* to Nextcloud (like task completions or title updates), the application relies on a Webhook configured in Taiga.

**Automated Webhook Registration:**
When you save your configuration in the Web App UI, the application will automatically connect to the Taiga API using your provided token and register a new webhook pointing to itself. It infers its own URL based on your browser request.

If you ever change your domain or IP address, simply visit the configuration page again and click "Save Configuration". The application will automatically delete the old webhook and register a new one pointing to the correct address.

*Note: The automatic webhook registration process works best when the Flask app and Taiga are hosted on the same server, or when the Flask application is exposed to the public internet.*

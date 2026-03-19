import requests

def create_webhook(taiga_url, token, project_id, webhook_url):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # List existing webhooks
    res = requests.get(f"{taiga_url}/api/v1/webhooks?project={project_id}", headers=headers)
    if res.status_code == 200:
        for wh in res.json():
            if wh['url'] == webhook_url:
                # Delete existing
                print(f"Deleting existing webhook {wh['id']}")
                requests.delete(f"{taiga_url}/api/v1/webhooks/{wh['id']}", headers=headers)

    # Create new
    payload = {
        "project": project_id,
        "url": webhook_url,
        "name": "Nextcloud Sync Webhook",
        "key": "some-secret-key"
    }
    res = requests.post(f"{taiga_url}/api/v1/webhooks", json=payload, headers=headers)
    if res.status_code == 201:
        print("Webhook created successfully")
    else:
        print(f"Failed to create webhook: {res.text}")

print("Syntax OK")

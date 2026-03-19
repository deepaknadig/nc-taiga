import requests

url = "http://localhost:5000/taiga-webhook"

payload = {
    "action": "change",
    "type": "task",
    "data": {
        "id": 1234,
        "is_closed": True,
        "subject": "Updated Title",
        "description": "Updated Description",
        "user_story": 12
    },
    "change": {
        "diff": {
            "status": ["In Progress", "Closed"],
            "subject": ["Old Title", "Updated Title"]
        }
    }
}

try:
    response = requests.post(url, json=payload)
    print(response.status_code)
    print(response.json())
except Exception as e:
    print(f"Error: {e}")

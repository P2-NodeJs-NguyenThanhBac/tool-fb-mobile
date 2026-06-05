import requests
import json
import time

BASE = "http://192.168.1.35:8000"
DEVICE_ID = "UWJJOJLB85SO7LIZ"
USER_ID = "0971335869"

payload = {
    "type": "test_clipboard_termux",
    "user_id": USER_ID,
    "device_serial": DEVICE_ID,
    "params": {}
}

r = requests.post(f"{BASE}/api/command", json=payload, timeout=15)
print("STATUS:", r.status_code)
print("RESP:", r.text)

try:
    data = r.json()
    job_id = data.get("id")
except Exception:
    job_id = None

if job_id:
    print("JOB_ID =", job_id)
    print("Polling job state...")
    for i in range(20):
        time.sleep(1)
        rr = requests.get(f"{BASE}/api/jobs/{job_id}", timeout=10)
        print(f"[{i+1}] {rr.status_code} {rr.text}")
        if rr.status_code == 200:
            j = rr.json()
            if j.get("status") not in ("pending", "in_progress"):
                break
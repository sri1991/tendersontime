
import requests

hosts = [
    "http://136.114.154.210:8000",
]

endpoints = [
    "/api/v1/heartbeat",
    "/api/v1/collections",
    "/api/v1",
    "/heartbeat",
    "/",
    "/docs"
]

for host in hosts:
    print(f"--- Probing {host} ---")
    for ep in endpoints:
        url = f"{host}{ep}"
        try:
            resp = requests.get(url, timeout=2)
            print(f"{ep}: {resp.status_code} - {resp.text[:100]}")
        except Exception as e:
            print(f"{ep}: Failed - {e}")

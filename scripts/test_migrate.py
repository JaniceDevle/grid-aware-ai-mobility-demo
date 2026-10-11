import requests

BASE = "http://127.0.0.1:8000"
HEADERS = {"X-API-Key": "demo-key", "Content-Type": "application/json"}

# 创建 workload
r = requests.post(f"{BASE}/workloads", headers=HEADERS, json={
    "name": "debug", "owner": "alice",
    "model_variant": "small", "site": "edge",
})
print("CREATE", r.status_code, r.text)
wid = r.json()["id"]
print("ID =", wid)

# 先推理一次
r = requests.post(f"{BASE}/workloads/{wid}/infer", headers=HEADERS,
                  json={"prompt": "warmup"})
print("INFER", r.status_code, r.text)

# 迁移
r = requests.post(f"{BASE}/workloads/{wid}/migrate", headers=HEADERS, json={
    "target_site": "cloud",
    "target_model_variant": "large",
    "budget_ms": 200,
})
print("MIGRATE", r.status_code, r.text)

# 查状态
r = requests.get(f"{BASE}/workloads/{wid}", headers=HEADERS)
print("GET", r.status_code, r.text)
"""Smoke tests. Requires three services running (8000, 8001, 8002)."""
import requests
import pytest

BASE = "http://127.0.0.1:8000"
H = {"X-API-Key": "demo-key", "Content-Type": "application/json"}


def _new(site="edge", model="small"):
    r = requests.post(f"{BASE}/workloads", headers=H,
                      json={"name": "t", "owner": "alice",
                            "model_variant": model, "site": site})
    r.raise_for_status()
    return r.json()["id"]


def test_health():
    assert requests.get(f"{BASE}/health").json()["ok"]


def test_testbeds_reachable():
    d = requests.get(f"{BASE}/testbeds").json()
    assert d["edge"]["ok"] and d["cloud"]["ok"]


def test_create_and_infer():
    wid = _new()
    r = requests.post(f"{BASE}/workloads/{wid}/infer",
                      headers=H, json={"prompt": "hi"})
    b = r.json()
    assert b["counter"] == 1
    assert b["site"] == "edge" and b["model_variant"] == "small"


def test_migrate_success_preserves_state():
    wid = _new()
    requests.post(f"{BASE}/workloads/{wid}/infer",
                  headers=H, json={"prompt": "a"}).raise_for_status()
    r = requests.post(f"{BASE}/workloads/{wid}/migrate", headers=H,
                      json={"target_site": "cloud",
                            "target_model_variant": "large",
                            "budget_ms": 200}).json()
    assert r["rolled_back"] is False
    assert r["to_site"] == "cloud" and r["to_model"] == "large"
    s = requests.get(f"{BASE}/workloads/{wid}").json()
    assert s["counter"] == 1
    assert s["site"] == "cloud" and s["model_variant"] == "large"


def test_migrate_budget_exceeded_rolls_back():
    wid = _new()
    requests.post(f"{BASE}/workloads/{wid}/infer",
                  headers=H, json={"prompt": "a"}).raise_for_status()
    r = requests.post(f"{BASE}/workloads/{wid}/migrate", headers=H,
                      json={"target_site": "cloud",
                            "target_model_variant": "large",
                            "budget_ms": 1}).json()
    assert r["rolled_back"] is True
    s = requests.get(f"{BASE}/workloads/{wid}").json()
    assert s["site"] == "edge" and s["counter"] == 1


def test_checkpoint_restore_roundtrip():
    wid = _new()
    requests.post(f"{BASE}/workloads/{wid}/infer",
                  headers=H, json={"prompt": "a"}).raise_for_status()
    requests.post(f"{BASE}/workloads/{wid}/infer",
                  headers=H, json={"prompt": "b"}).raise_for_status()

    blob = requests.post(f"{BASE}/workloads/{wid}/checkpoint",
                         headers=H).json()
    assert blob["workload_id"] == wid
    assert blob["size_bytes"] > 0
    assert blob["state"]["counter"] == 2

    # restore into same id
    r = requests.post(f"{BASE}/workloads/{wid}/restore",
                      headers=H, json=blob)
    assert r.status_code == 200
    s = r.json()
    assert s["counter"] == 2
    assert s["history"] == ["a", "b"]


def test_security_policy_denies_bad_key():
    wid = _new()
    r = requests.post(f"{BASE}/workloads/{wid}/infer",
                      headers={"X-API-Key": "wrong",
                               "Content-Type": "application/json"},
                      json={"prompt": "x"})
    assert r.status_code == 403


def test_auto_migrate_prefers_green_site():
    requests.post(f"{BASE}/grid/signal?site=edge", headers=H, json={
        "zone": "berlin-edge", "price": 0.20,
        "renewable_ratio": 0.85, "carbon_intensity": 100.0}).raise_for_status()
    requests.post(f"{BASE}/grid/signal?site=cloud", headers=H, json={
        "zone": "frankfurt-cloud", "price": 0.10,
        "renewable_ratio": 0.30, "carbon_intensity": 350.0}).raise_for_status()
    requests.post(f"{BASE}/policy", headers=H, json={
        "w_renewable": 0.4, "w_price": 0.3, "w_carbon": 0.3,
        "max_carbon": 500, "min_renewable": 0.0,
        "large_model_allowed_below_carbon": 300,
        "price_ref_max": 0.5, "carbon_ref_max": 600}).raise_for_status()

    wid = _new(site="cloud")
    r = requests.post(f"{BASE}/grid/auto-migrate/{wid}",
                      headers=H, params={"budget_ms": 200}).json()
    assert r["to_site"] == "edge"
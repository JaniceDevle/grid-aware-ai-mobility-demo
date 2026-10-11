import json
import os
import time
import uuid
from datetime import datetime
from typing import Dict, List

import httpx
from fastapi import FastAPI, Header, HTTPException

from app.models import (
    GridSignal, GridPolicy,
    WorkloadCreate, WorkloadState,
    InferRequest, InferResponse,
    MigrateRequest, MigrateResponse,
    CheckpointBlob, PayloadInject,
    SiteName,
)
from app.policy import select_target, select_model_variant

app = FastAPI(title="Grid-Aware AI Workload Mobility Demo")

NODE_URLS = {
    "edge": os.getenv("EDGE_URL", "http://127.0.0.1:8001"),
    "cloud": os.getenv("CLOUD_URL", "http://127.0.0.1:8002"),
}

ADMIN_KEY = os.getenv("ADMIN_KEY", "demo-key")


def check_admin(api_key: str) -> bool:
    return api_key == ADMIN_KEY

# simulated edge-cloud link bandwidth for transfer cost
LINK_BYTES_PER_SEC = 10 * 1024 * 1024      # 10 MB/s
SERIALIZE_BYTES_PER_SEC = 200 * 1024 * 1024  # 200 MB/s

WORKLOADS: Dict[str, WorkloadState] = {}

GRID_SIGNALS: Dict[str, GridSignal] = {
    "edge": GridSignal(zone="berlin-edge", price=0.18,
                       renewable_ratio=0.55, carbon_intensity=210),
    "cloud": GridSignal(zone="frankfurt-cloud", price=0.12,
                        renewable_ratio=0.35, carbon_intensity=320),
}

GRID_POLICY = GridPolicy()
POLICY_LOG: List[dict] = []

METRICS = {
    "migrations_total": 0,
    "migrations_success": 0,
    "migration_failures": 0,
    "migrations_rolled_back": 0,
    "migration_budget_exceeded": 0,
    "inferences_total": 0,
    "policy_denials": 0,
    "migration_ms_samples": [],
    "serialize_ms_samples": [],
    "transfer_ms_samples": [],
    "restore_ms_samples": [],
    "checkpoint_sizes_bytes": [],
}


def log_event(event_type: str, **kwargs):
    POLICY_LOG.append({"ts": datetime.utcnow().isoformat(),
                       "event": event_type, **kwargs})
    if len(POLICY_LOG) > 300:
        POLICY_LOG.pop(0)


def check_policy(workload: WorkloadState, api_key: str) -> bool:
    return api_key == "demo-key" or api_key == workload.owner


# ---------------- basic endpoints ----------------

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/sites")
def sites():
    return GRID_SIGNALS


@app.get("/testbeds")
def testbeds():
    result = {}
    for site, url in NODE_URLS.items():
        try:
            r = httpx.get(f"{url}/health", timeout=2.0)
            result[site] = r.json()
        except httpx.HTTPError as e:
            result[site] = {"ok": False, "error": str(e)}
    return result


# ---------------- policy ----------------

@app.get("/policy")
def get_policy():
    return GRID_POLICY


@app.post("/policy")
def set_policy(policy: GridPolicy, x_api_key: str = Header("demo-key")):
    if not check_admin(x_api_key):
        raise HTTPException(403, "security policy denied")
    global GRID_POLICY
    GRID_POLICY = policy
    log_event("policy_updated", policy=policy.model_dump())
    return GRID_POLICY

@app.get("/policy/log")
def get_log(limit: int = 50):
    return POLICY_LOG[-limit:]


# ---------------- workload lifecycle ----------------

@app.post("/workloads", response_model=WorkloadState)
def create_workload(req: WorkloadCreate):
    wid = str(uuid.uuid4())[:8]
    wl = WorkloadState(id=wid, name=req.name, owner=req.owner,
                       site=req.site, model_variant=req.model_variant)
    WORKLOADS[wid] = wl
    return wl


@app.get("/workloads/{workload_id}", response_model=WorkloadState)
def get_workload(workload_id: str):
    wl = WORKLOADS.get(workload_id)
    if not wl:
        raise HTTPException(404, "workload not found")
    return wl


# ---------------- payload injection (for scaling experiments) ----------------

@app.post("/workloads/{workload_id}/payload")
def inject_payload(workload_id: str, req: PayloadInject,
                   x_api_key: str = Header("demo-key")):
    wl = WORKLOADS.get(workload_id)
    if not wl:
        raise HTTPException(404, "workload not found")
    if not check_policy(wl, x_api_key):
        raise HTTPException(403, "security policy denied")
    wl.payload = "x" * req.size_bytes
    return {"ok": True, "payload_bytes": len(wl.payload)}


# ---------------- checkpoint / restore ----------------

@app.post("/workloads/{workload_id}/checkpoint", response_model=CheckpointBlob)
def checkpoint(workload_id: str, x_api_key: str = Header("demo-key")):
    wl = WORKLOADS.get(workload_id)
    if not wl:
        raise HTTPException(404, "workload not found")
    if not check_policy(wl, x_api_key):
        METRICS["policy_denials"] += 1
        raise HTTPException(403, "security policy denied")

    payload = wl.model_dump_json()
    state = json.loads(payload)
    return CheckpointBlob(
        workload_id=wl.id,
        state=state,
        size_bytes=len(payload.encode("utf-8")),
        created_at=datetime.utcnow().isoformat(),
    )


@app.post("/workloads/{workload_id}/restore", response_model=WorkloadState)
def restore(workload_id: str, blob: CheckpointBlob,
            x_api_key: str = Header("demo-key")):
    if not check_admin(x_api_key):
        METRICS["policy_denials"] += 1
        raise HTTPException(403, "security policy denied")
    if blob.workload_id != workload_id:
        raise HTTPException(400, "blob does not match workload")
    wl = WorkloadState(**blob.state)
    WORKLOADS[workload_id] = wl
    log_event("restore_completed", workload_id=workload_id,
              size_bytes=blob.size_bytes)
    return wl


# ---------------- inference ----------------

@app.post("/workloads/{workload_id}/infer", response_model=InferResponse)
def infer(workload_id: str, req: InferRequest,
          x_api_key: str = Header("demo-key")):
    wl = WORKLOADS.get(workload_id)
    if not wl:
        raise HTTPException(404, "workload not found")
    if not check_policy(wl, x_api_key):
        METRICS["policy_denials"] += 1
        log_event("policy_denied", action="infer", workload_id=workload_id)
        raise HTTPException(403, "security policy denied")

    url = NODE_URLS[wl.site]
    try:
        r = httpx.post(f"{url}/execute", timeout=5.0, json={
            "workload_id": wl.id,
            "prompt": req.prompt,
            "model_variant": wl.model_variant,
        })
        r.raise_for_status()
    except httpx.HTTPError as e:
        log_event("node_unreachable", site=wl.site, error=str(e))
        raise HTTPException(503, f"node {wl.site} unreachable")

    node = r.json()
    wl.counter += 1
    wl.history.append(req.prompt)
    if len(wl.history) > 10:
        wl.history = wl.history[-10:]
    METRICS["inferences_total"] += 1

    return InferResponse(
        workload_id=wl.id, site=wl.site, model_variant=wl.model_variant,
        output=node["output"], latency_ms=node["latency_ms"],
        counter=wl.counter,
    )


# ---------------- migration (staged, instrumented) ----------------

def _do_migrate(wl: WorkloadState, target_site: str, target_model: str,
                budget_ms: float, decision_log=None) -> MigrateResponse:
    snapshot = wl.model_copy(deep=True)
    from_site, from_model = wl.site, wl.model_variant
    METRICS["migrations_total"] += 1

    t_start = time.perf_counter()

    # Stage 1: serialize (checkpoint)
    t = time.perf_counter()
    payload = wl.model_dump_json()
    size_bytes = len(payload.encode("utf-8"))
    time.sleep(size_bytes / SERIALIZE_BYTES_PER_SEC)
    serialize_ms = (time.perf_counter() - t) * 1000

    # Stage 2: transfer (simulated network with finite bandwidth)
    t = time.perf_counter()
    transfer_s = size_bytes / LINK_BYTES_PER_SEC
    time.sleep(max(transfer_s, 0.001))       # at least 1 ms
    transfer_ms = (time.perf_counter() - t) * 1000

    # Stage 3: restore on target
    t = time.perf_counter()
    restored = WorkloadState.model_validate_json(payload)
    time.sleep(size_bytes / SERIALIZE_BYTES_PER_SEC)
    restore_ms = (time.perf_counter() - t) * 1000

    total_ms = (time.perf_counter() - t_start) * 1000

    # Invariant check: state continuity
    if restored.counter != snapshot.counter:
        WORKLOADS[wl.id] = snapshot
        METRICS["migration_failures"] += 1
        log_event("migration_failed", workload_id=wl.id, reason="state lost")
        raise HTTPException(500, "state lost during migration")

    # Budget check
    if total_ms > budget_ms:
        WORKLOADS[wl.id] = snapshot
        METRICS["migrations_rolled_back"] += 1
        METRICS["migration_budget_exceeded"] += 1
        log_event("migration_rollback", workload_id=wl.id,
                  reason="budget exceeded",
                  total_ms=round(total_ms, 3), budget_ms=budget_ms)
        return MigrateResponse(
            workload_id=wl.id,
            from_site=from_site, to_site=from_site,
            from_model=from_model, to_model=from_model,
            migration_ms=round(total_ms, 3), budget_ms=budget_ms,
            policy_ok=True, rolled_back=True,
            message=f"budget {budget_ms}ms exceeded, rolled back",
            decision_log=decision_log,
        )

    # Commit
    restored.site = target_site
    restored.model_variant = target_model
    WORKLOADS[wl.id] = restored

    METRICS["migrations_success"] += 1
    METRICS["migration_ms_samples"].append(total_ms)
    METRICS["serialize_ms_samples"].append(serialize_ms)
    METRICS["transfer_ms_samples"].append(transfer_ms)
    METRICS["restore_ms_samples"].append(restore_ms)
    METRICS["checkpoint_sizes_bytes"].append(size_bytes)

    log_event("migration_success", workload_id=wl.id,
              from_site=from_site, to_site=target_site,
              from_model=from_model, to_model=target_model,
              total_ms=round(total_ms, 3),
              serialize_ms=round(serialize_ms, 3),
              transfer_ms=round(transfer_ms, 3),
              restore_ms=round(restore_ms, 3),
              size_bytes=size_bytes)

    return MigrateResponse(
        workload_id=wl.id,
        from_site=from_site, to_site=target_site,
        from_model=from_model, to_model=target_model,
        migration_ms=round(total_ms, 3), budget_ms=budget_ms,
        policy_ok=True, rolled_back=False,
        message="stateful migration completed",
        decision_log=decision_log,
    )


@app.post("/workloads/{workload_id}/migrate", response_model=MigrateResponse)
def migrate(workload_id: str, req: MigrateRequest,
            x_api_key: str = Header("demo-key")):
    wl = WORKLOADS.get(workload_id)
    if not wl:
        raise HTTPException(404, "workload not found")
    if not check_policy(wl, x_api_key):
        METRICS["policy_denials"] += 1
        raise HTTPException(403, "security policy denied")

    target_site = req.target_site or wl.site
    target_model = req.target_model_variant or wl.model_variant
    return _do_migrate(wl, target_site, target_model, req.budget_ms)


# ---------------- grid-driven auto-migration ----------------

@app.post("/grid/signal")
def update_grid_signal(site: SiteName, signal: GridSignal,
                       x_api_key: str = Header("demo-key")):
    if not check_admin(x_api_key):
        raise HTTPException(403, "security policy denied")
    GRID_SIGNALS[site] = signal
    log_event("grid_signal_updated", site=site, signal=signal.model_dump())
    return {"ok": True, "site": site, "signal": signal}


@app.post("/grid/auto-migrate/{workload_id}", response_model=MigrateResponse)
def auto_migrate(workload_id: str, budget_ms: float = 200.0,
                 x_api_key: str = Header("demo-key")):
    wl = WORKLOADS.get(workload_id)
    if not wl:
        raise HTTPException(404, "workload not found")
    if not check_policy(wl, x_api_key):
        METRICS["policy_denials"] += 1
        raise HTTPException(403, "security policy denied")

    target_site, best_score, decision_log = select_target(
        GRID_SIGNALS, GRID_POLICY, wl.site)
    target_model = select_model_variant(GRID_SIGNALS[target_site], GRID_POLICY)

    log_event("auto_migrate_decision", workload_id=workload_id,
              target_site=target_site, target_model=target_model,
              best_score=round(best_score, 4), decision_log=decision_log)

    if target_site == wl.site and target_model == wl.model_variant:
        return MigrateResponse(
            workload_id=wl.id,
            from_site=wl.site, to_site=wl.site,
            from_model=wl.model_variant, to_model=wl.model_variant,
            migration_ms=0.0, budget_ms=budget_ms,
            policy_ok=True, rolled_back=False,
            message="no migration needed, current placement already optimal",
            decision_log=decision_log,
        )

    return _do_migrate(wl, target_site, target_model, budget_ms, decision_log)


# ---------------- metrics ----------------

@app.get("/metrics")
def metrics():
    return {k: v for k, v in METRICS.items() if not k.endswith("_samples")}


@app.get("/metrics/summary")
def metrics_summary():
    def stats(xs):
        if not xs:
            return None
        return {"count": len(xs), "avg": round(sum(xs) / len(xs), 3),
                "min": round(min(xs), 3), "max": round(max(xs), 3)}

    return {
        "migrations_total": METRICS["migrations_total"],
        "migrations_success": METRICS["migrations_success"],
        "migrations_rolled_back": METRICS["migrations_rolled_back"],
        "budget_exceeded": METRICS["migration_budget_exceeded"],
        "total_ms": stats(METRICS["migration_ms_samples"]),
        "serialize_ms": stats(METRICS["serialize_ms_samples"]),
        "transfer_ms": stats(METRICS["transfer_ms_samples"]),
        "restore_ms": stats(METRICS["restore_ms_samples"]),
        "checkpoint_size_bytes": stats(METRICS["checkpoint_sizes_bytes"]),
    }
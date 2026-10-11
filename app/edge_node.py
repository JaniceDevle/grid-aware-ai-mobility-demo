"""Edge compute node - stateless executor, listens on 8001."""
import time
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Edge Compute Node")


class ExecuteRequest(BaseModel):
    workload_id: str
    prompt: str
    model_variant: str


class ExecuteResult(BaseModel):
    node: str
    zone: str
    workload_id: str
    output: str
    latency_ms: float
    model_variant: str


@app.get("/health")
def health():
    return {"node": "edge", "zone": "berlin-edge", "ok": True}


@app.post("/execute", response_model=ExecuteResult)
def execute(req: ExecuteRequest):
    start = time.perf_counter()
    if req.model_variant == "small":
        time.sleep(0.01)
    else:
        time.sleep(0.05)
    latency_ms = (time.perf_counter() - start) * 1000

    return ExecuteResult(
        node="edge",
        zone="berlin-edge",
        workload_id=req.workload_id,
        output=f"[edge/{req.model_variant}] {req.prompt}",
        latency_ms=round(latency_ms, 3),
        model_variant=req.model_variant,
    )
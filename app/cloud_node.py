"""Cloud compute node - stateless executor, listens on 8002."""
import time
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Cloud Compute Node")


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
    return {"node": "cloud", "zone": "frankfurt-cloud", "ok": True}


@app.post("/execute", response_model=ExecuteResult)
def execute(req: ExecuteRequest):
    start = time.perf_counter()
    if req.model_variant == "small":
        time.sleep(0.012)   # cloud 稍慢一点，模拟网络
    else:
        time.sleep(0.055)
    latency_ms = (time.perf_counter() - start) * 1000

    return ExecuteResult(
        node="cloud",
        zone="frankfurt-cloud",
        workload_id=req.workload_id,
        output=f"[cloud/{req.model_variant}] {req.prompt}",
        latency_ms=round(latency_ms, 3),
        model_variant=req.model_variant,
    )
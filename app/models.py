from pydantic import BaseModel, Field
from typing import Literal, List, Optional
from datetime import datetime

SiteName = Literal["edge", "cloud"]
ModelVariant = Literal["small", "large"]


class GridSignal(BaseModel):
    zone: str
    price: float
    renewable_ratio: float
    carbon_intensity: float


class GridPolicy(BaseModel):
    w_renewable: float = 0.4
    w_price: float = 0.3
    w_carbon: float = 0.3
    max_carbon: float = 350.0
    min_renewable: float = 0.0
    large_model_allowed_below_carbon: float = 300.0
    price_ref_max: float = 0.5
    carbon_ref_max: float = 600.0


class WorkloadCreate(BaseModel):
    name: str
    owner: str
    model_variant: ModelVariant = "small"
    site: SiteName = "edge"


class WorkloadState(BaseModel):
    id: str
    name: str
    owner: str
    site: SiteName
    model_variant: ModelVariant
    counter: int = 0
    history: List[str] = []
    payload: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class InferRequest(BaseModel):
    prompt: str


class InferResponse(BaseModel):
    workload_id: str
    site: SiteName
    model_variant: ModelVariant
    output: str
    latency_ms: float
    counter: int


class MigrateRequest(BaseModel):
    target_site: Optional[SiteName] = None
    target_model_variant: Optional[ModelVariant] = None
    budget_ms: float = 200.0


class MigrateResponse(BaseModel):
    workload_id: str
    from_site: SiteName
    to_site: SiteName
    from_model: ModelVariant
    to_model: ModelVariant
    migration_ms: float
    budget_ms: float
    policy_ok: bool
    rolled_back: bool = False
    message: str
    decision_log: Optional[List[dict]] = None


class CheckpointBlob(BaseModel):
    workload_id: str
    state: dict
    size_bytes: int
    created_at: str


class PayloadInject(BaseModel):
    size_bytes: int
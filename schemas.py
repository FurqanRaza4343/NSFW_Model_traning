from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

Decision = Literal["approve", "review", "reject"]


class ModerationResponse(BaseModel):
    decision: Decision
    nsfw_score: float                      # explicit-content score (max over models)
    suggestive_score: float = 0.0          # nudity / suggestive score
    scores: Dict[str, float]               # {"normal": 1-nsfw, "nsfw": nsfw}
    details: Dict[str, Dict[str, float]] = Field(default_factory=dict)  # per-model scores
    reasons: List[str] = Field(default_factory=list)
    model: str
    latency_ms: float


class ErrorDetail(BaseModel):
    detail: str


class UsageLimits(BaseModel):
    per_minute: Optional[int] = None
    per_day: Optional[int] = None


class UsageCounts(BaseModel):
    minute: int
    day: int
    total: int


class UsageResponse(BaseModel):
    name: str
    limits: Optional[UsageLimits] = None
    used: Optional[UsageCounts] = None
    unlimited: Optional[bool] = None
    trial: Optional[Dict[str, int]] = None      # {"limit": 2, "used": 1} for free-trial keys
    note: Optional[str] = None

from typing import Dict, List, Literal
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


class ErrorResponse(BaseModel):
    error: str

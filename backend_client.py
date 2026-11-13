"""Client your FastAPI backend uses to call the Modal moderation endpoint.

Rules:
- The frontend never talks to Modal. Only the backend (which holds the API key) does.
- FAIL PRIVATE: on any error (timeout, 5xx, bad key, network) the image is NOT published.

Statuses returned:
    publish         decision "approve"
    pending_review  decision "review"  (a human must look at it)
    rejected        decision "reject"
    private         anything went wrong -> keep the image private
"""
import asyncio
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx

STATUS_BY_DECISION = {"approve": "publish", "review": "pending_review", "reject": "rejected"}


@dataclass
class ModerationResult:
    status: str                       # publish | pending_review | rejected | private
    decision: Optional[str] = None    # raw decision from the service
    nsfw_score: Optional[float] = None
    suggestive_score: Optional[float] = None
    reasons: list = field(default_factory=list)
    error: Optional[str] = None


class ModerationClient:
    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_s: float = 60.0,   # cold start is ~10-15 s, so keep this generous
        retries: int = 2,
        transport: Optional[httpx.AsyncBaseTransport] = None,  # for tests
    ):
        self.url = (url or os.environ["MODERATION_URL"]).rstrip("/")
        self.api_key = api_key or os.environ["MODERATION_API_KEY"]
        self.timeout_s = timeout_s
        self.retries = retries
        self._transport = transport

    async def check(self, image_bytes: bytes, filename: str = "image") -> ModerationResult:
        last_error = "unknown error"
        for attempt in range(self.retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_s, transport=self._transport) as client:
                    r = await client.post(
                        f"{self.url}/moderate",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        files={"file": (filename, image_bytes)},
                    )
                if r.status_code == 200:
                    body = r.json()
                    status = STATUS_BY_DECISION.get(body.get("decision"))
                    if status is None:  # unexpected payload -> never publish
                        return ModerationResult("private", error=f"unexpected decision: {body.get('decision')}")
                    return ModerationResult(
                        status=status,
                        decision=body["decision"],
                        nsfw_score=body.get("nsfw_score"),
                        suggestive_score=body.get("suggestive_score"),
                        reasons=body.get("reasons", []),
                    )
                if 400 <= r.status_code < 500:  # bad key / bad image: retrying will not help
                    return ModerationResult("private", error=f"HTTP {r.status_code}: {r.text[:200]}")
                last_error = f"HTTP {r.status_code}"  # 5xx -> retry
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_error = f"{type(e).__name__}: {e}"
            if attempt < self.retries:
                await asyncio.sleep(1.5 * (attempt + 1))
        return ModerationResult("private", error=last_error)

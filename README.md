# NSFW Image Moderation (Modal + 3-model ensemble)

A small image-moderation microservice. Your backend sends an image, the service answers
**approve / review / reject** with scores. Runs on [Modal](https://modal.com) (CPU, scales to zero).
Includes a Streamlit test UI.

```
Website -> your FastAPI backend -> Modal endpoint (POST /moderate) -> 3 models -> decision
```

The frontend must never call Modal directly; only the backend holds the API key.

## Models (all Apache-2.0, public, not gated)

| Model | Role |
|---|---|
| `Falconsai/nsfw_image_detection` | ViT-base, normal / nsfw |
| `Marqo/nsfw-image-detection-384` | tiny ViT (timm), NSFW / SFW; catches partial nudity |
| `LukeJacob2023/nsfw-image-detector` | ViT-base, 5 classes (drawings, hentai, neutral, porn, adult) |

Why an ensemble: in our tests Falconsai alone scored a semi-nude artistic photo as 0.0005 NSFW (a miss).
Marqo and the "adult" class of the 5-class model catch it.

## Decision policy (provisional - calibrate on your own images)

- `nsfw_score` = max(Falconsai nsfw, Marqo NSFW, LukeJacob porn + hentai)
- `suggestive_score` = LukeJacob "adult"

| Condition | Decision |
|---|---|
| `nsfw_score >= 0.50` | `reject` |
| `nsfw_score >= 0.30` or `suggestive_score >= 0.50` | `review` (do not publish yet) |
| otherwise | `approve` |

Thresholds live in `config.py`. Policy: nudity (including artistic) is blocked or reviewed, never auto-approved.
On any error or timeout, keep the image **private**.

The values were set from a handful of images only. Use the Streamlit UI (sliders + "your label" option)
with 50-100 varied, legal images before trusting them.

## Files

```
modal_app.py       Modal app: image, Volume, /moderate endpoint, Streamlit UI function
classifier.py      Loads the 3 models once, validates image, runs inference
config.py          Model ids, limits, thresholds, decide()
schemas.py         Response schema
ui_app.py          Streamlit test UI
keys.py            Customer API keys: hashing, limits, storage logic
leads.py           Registration (leads), free trial, access-code protection, Google Sheet webhook
manage_leads.py    Admin CLI: list / export / delete registered people
docs/leads_apps_script.gs  Google Apps Script that receives leads into a Sheet
manage_keys.py     Admin CLI: create / list / revoke keys
INTEGRATION.md     Short guide to give to customers (endpoint, limits, examples)
docs/index.html    Full documentation site, served by the API at its root URL (/)
backend_client.py  Client for YOUR backend: retries, timeouts, fails private
backend_example.py Example FastAPI /upload route using the client
evaluate.py        Threshold calibration on your labelled images (laptop)
local_test.py      Run the models on your laptop, no Modal needed
tests/test_api.py      API tests against the deployed endpoint
tests/test_backend.py  Unit tests for backend_client (no network)
tests/test_keys.py     Unit tests for key logic (no network)
tests/test_leads.py    Unit tests for leads, trial and access-code logic (no network)
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
modal setup                       # log in to Modal (a card may be required)

modal secret create moderation-auth MODERATION_API_KEY=<long-random-string>
modal secret create ui-auth UI_PASSWORD=<strong-password>

modal run modal_app.py            # downloads the models into a Modal Volume
modal deploy modal_app.py         # prints the API URL and the UI URL
```

Copy `.env.example` to `.env` and fill in `MODERATION_URL` and `MODERATION_API_KEY`. Never commit `.env`.

## Use the API

```bash
curl -X POST "$MODERATION_URL/moderate" \
  -H "Authorization: Bearer $MODERATION_API_KEY" \
  -F "file=@photo.jpg"
```

Response (example):

```json
{
  "decision": "reject",
  "nsfw_score": 0.74,
  "suggestive_score": 0.99,
  "scores": {"normal": 0.26, "nsfw": 0.74},
  "details": {"falconsai": {}, "marqo": {}, "luke": {}},
  "reasons": ["marqo_nsfw=0.74", "luke_adult=1.00"],
  "model": "ensemble(...)",
  "latency_ms": 1075.0
}
```

Errors: `401` wrong/missing key, `400` empty, corrupt or too large (> 5 MB) image. `GET /health` for a liveness check.

## Test

```bash
pytest tests/test_api.py          # needs .env with MODERATION_URL and MODERATION_API_KEY
python local_test.py <folder>     # local run; set HF_HOME to where the models are cached
```

The Streamlit UI is deployed together with the API (password protected). Locally: `streamlit run ui_app.py`.

## Documentation

The API serves its own documentation (no login needed):

- `/` : the documentation site (product overview, quickstart, authentication, every endpoint, response
  and error reference, rate limits, code examples in 8 languages, best practices, models, FAQ, changelog),
  with a "try it" box. Source: `docs/index.html`. Numbers such as limits and thresholds are filled in from
  `config.py`, so they never go out of date.
- `/docs` : interactive Swagger UI with an **Authorize** button; `/redoc` : reference view;
  `/openapi.json` : machine-readable spec.

## Public access: registration, free trial and access code

The web UI is public. Before the model runs, a visitor registers (name, email, optional phone):

- **Free trial:** `TRIAL_CHECKS` (2) image checks per person in total, shared between the web app and the API.
  A global daily cap (`GLOBAL_TRIAL_PER_DAY`) protects the free cloud credits.
- **Access code:** a person who enters the owner's code (Modal secret `unlimited-code`) gets unlimited use.
  The code is verified with brute-force protection and is never stored in the repository.
- **API keys:** a registered person can generate a key in the UI (API access tab). Free-trial keys share the
  person's trial; keys created while the access code is unlocked have no limits. Owner-issued keys
  (`manage_keys.py` or the UI Admin tab) can have custom limits.
- **Leads:** name, email and phone are saved in a `modal.Dict` (backed up daily to the Volume) and, if configured,
  appended to a Google Sheet through an Apps Script web app (`docs/leads_apps_script.gs`). Images are never stored.
- **Privacy:** the consent checkbox and the privacy note are optional (`REQUIRE_CONSENT`, `SHOW_PRIVACY_NOTICE` in
  `config.py`, both off by default). Image retention needs explicit consent, so with the checkbox off nothing
  but the lead details and per-check records is kept for web registrations. Delete a person with `python manage_leads.py delete --email ...`
  (or the Admin tab). Export with `python manage_leads.py export leads.csv`.
- Your own key (Modal secret `moderation-auth`) stays valid as the admin key and has no limits.
- Keys and leads live in a `modal.Dict` (expires after 7 idle days), so a daily job (`maintain_keys`) refreshes
  them, keeps a backup in the Volume and retries Google Sheet writes that failed.

Secrets to create once:

```bash
modal secret create moderation-auth MODERATION_API_KEY=<long-random-string>
modal secret create ui-admin UI_ADMIN_PASSWORD=<admin-password>
modal secret create unlimited-code UNLIMITED_ACCESS_CODE=<access-code>
modal secret create leads-sink LEADS_WEBHOOK_URL=<apps-script-web-app-url> LEADS_WEBHOOK_TOKEN=<random-token>
```

See `INTEGRATION.md` for what to hand to customers (limits, errors, code examples).

## Use it from your backend

```python
from backend_client import ModerationClient
moderation = ModerationClient()                 # reads MODERATION_URL / MODERATION_API_KEY
result = await moderation.check(image_bytes, filename)
# result.status: publish | pending_review | rejected | private
```

`private` means something went wrong (timeout, 5xx, bad key): the image must NOT be published.
See `backend_example.py` for a complete FastAPI route.

## Calibrate the thresholds

Put your own legal images in `eval_data/safe/` and `eval_data/unsafe/` (at least 50 each), then run
`python evaluate.py`. It lists the threshold combinations with zero false negatives and the fewest
false positives. Copy the chosen values into `config.py` and run `modal deploy modal_app.py`.

## Stability and cost

- Python packages are pinned to exact versions in `modal_app.py`.
- Models live in a Modal Volume and the service loads them in offline mode, so it never depends on
  Hugging Face being reachable at runtime. `modal run modal_app.py` refreshes the cache.
- `max_containers=3` caps parallel containers, so the maximum cost is capped too.
- 1 CPU core, 2 GiB memory (measured peak ~750 MiB), container stops 60 s after the last request.
- Measured: cold start ~11-14 s, warm model time ~1 s per image.
- Roughly $0.06 per running hour (estimate); a test session costs well under 1 cent. Set a spend limit
  in the Modal dashboard and check Usage there.
- Logs (scores, decision, latency; never image content): `modal app logs image-moderation`.

## Limitations

- These are classifiers, not guarantees: expect false positives and false negatives.
- Cold start: the first request after idle takes about 10-15 s.
- Rejected images are never stored. Approved / review images are kept (up to `IMAGE_RETENTION_DAYS`) only for people who agreed or keys the owner enabled.

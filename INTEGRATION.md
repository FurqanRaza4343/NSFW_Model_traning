# Integration guide

Send an image, get back `approve`, `review` or `reject`.

```
POST {BASE_URL}/v1/moderate
Authorization: Bearer <your API key>
Content-Type: multipart/form-data   (field name: file)
```

`BASE_URL` and your key are shown on the **API access** tab of the web UI, where you can also generate a key.

## Limits (per key)

| | Default |
|---|---|
| Requests per minute | 20 |
| Requests per day (resets 00:00 UTC) | 200 |
| Max image size | 5 MB |
| Formats | jpg, jpeg, jfif, png, webp, bmp, gif, tiff |

The first request after a quiet period takes ~10-15 s (cold start); normal requests take ~1-2 s. Use a client timeout of at least 60 s.

## Response

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

| `decision` | What to do |
|---|---|
| `approve` | Safe to publish |
| `review` | Do not publish yet; send to a human moderator |
| `reject` | Do not publish; keep private or delete |

## Errors

| Status | Meaning | Action |
|---|---|---|
| 400 | Empty, corrupt or too large image | Ask for another image |
| 401 | Missing, wrong or revoked key | Check the key |
| 403 | Key not allowed for this website (origin lock) | Check allowed websites |
| 429 | Rate limit exceeded | Wait `Retry-After` seconds |
| 5xx / timeout / network error | Service problem | Retry later and **keep the image private** |

**Golden rule: if anything goes wrong, never publish the image by default.**

## Other endpoints

- `GET /v1/usage` (with your key): your limits and current usage. Does not count as a request.
- `GET /health`: liveness check.
- `GET /docs`: interactive OpenAPI documentation.

## Examples

### curl
```bash
curl -X POST "$BASE_URL/v1/moderate" -H "Authorization: Bearer $API_KEY" -F "file=@photo.jpg"
```

### Python
```python
import requests

with open("photo.jpg", "rb") as f:
    r = requests.post(f"{BASE_URL}/v1/moderate",
                      headers={"Authorization": f"Bearer {API_KEY}"},
                      files={"file": f}, timeout=60)
r.raise_for_status()
print(r.json()["decision"])
```

### JavaScript (browser or Node 18+)
```js
const form = new FormData();
form.append("file", fileInput.files[0]);
const res = await fetch(`${BASE_URL}/v1/moderate`, {
  method: "POST",
  headers: { Authorization: `Bearer ${API_KEY}` },
  body: form,
});
if (!res.ok) { /* keep the image private */ }
const { decision } = await res.json();
```

### Android (Kotlin, OkHttp), Flutter, iOS (Swift)
See the **API access** tab of the UI. Those snippets are standard patterns that were not compiled here; adapt them to your project.

## Security

- **Call the API from your own backend** whenever you can. A key placed in website JavaScript or inside a mobile app can be extracted by anyone.
- If you must call from a browser, create the key with **allowed websites** so browser requests from other sites are rejected (this does not stop non-browser clients).
- Keys are stored only as hashes. A lost key cannot be recovered; ask the admin to revoke it and create a new one.
- Images are processed in memory and are not stored by this service.

## For the admin

```bash
python manage_keys.py create --name my-shop --per-minute 20 --per-day 200 --origins https://myshop.com
python manage_keys.py list
python manage_keys.py revoke --name my-shop
```

Revocation takes effect within about 30 seconds. The UI **Admin** tab (separate admin password) does the same.

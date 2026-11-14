"""Streamlit UI for the NSFW moderation service.

Tabs: 1) Test images  2) API access (endpoint, keys, examples)  3) About the models  4) Admin.
Runs on Modal (see modal_app.py) or locally:  streamlit run ui_app.py
The test tab calls the deployed Modal class directly, so no API key is needed there.
"""
import hmac
import io
import os
import time

import pandas as pd
import streamlit as st
from PIL import Image

import config

st.set_page_config(page_title="Image Moderation", page_icon="🛡️", layout="wide")

ALLOWED_TYPES = ["jpg", "jpeg", "jfif", "jpe", "png", "webp", "bmp", "gif", "tif", "tiff"]

# =====================================================================================
# Password gate (everyone who uses this UI needs the UI password)
# =====================================================================================
PASSWORD = os.environ.get("UI_PASSWORD", "")
if PASSWORD:
    if not st.session_state.get("authed"):
        st.title("🛡️ Image Moderation")
        pw = st.text_input("Password", type="password")
        if pw:
            if hmac.compare_digest(pw, PASSWORD):
                st.session_state["authed"] = True
                st.rerun()
            else:
                st.error("Wrong password")
        st.stop()


# =====================================================================================
# Helpers
# =====================================================================================
@st.cache_resource
def get_moderator():
    import modal

    return modal.Cls.from_name(config.APP_NAME, "Moderator")()


@st.cache_resource
def get_base_url() -> str:
    try:
        return get_moderator().web.get_web_url().rstrip("/")
    except Exception:
        return os.environ.get("API_BASE_URL", "https://YOUR-ENDPOINT.modal.run").rstrip("/")


@st.cache_resource
def get_store():
    import modal

    import keys as keylib

    return keylib.KeyStore(
        modal.Dict.from_name(config.KEYS_DICT, create_if_missing=True),
        modal.Dict.from_name(config.USAGE_DICT, create_if_missing=True),
    )


BADGE = {
    "approve": ("✅ SAFE (approve)", "green"),
    "review": ("🟡 UNCERTAIN (manual review)", "orange"),
    "reject": ("⛔ UNSAFE (reject)", "red"),
}

BASE_URL = get_base_url()
ENDPOINT = f"{BASE_URL}/v1/moderate"

st.title("🛡️ Image Moderation")
st.caption("Detects nude / explicit images with an ensemble of 3 open models. Runs on Modal cloud.")

tab_test, tab_api, tab_models, tab_admin = st.tabs(
    ["🧪 Test images", "🔑 API access", "🧠 About the models", "🛠️ Admin"]
)

# =====================================================================================
# Tab 1: test images
# =====================================================================================
with tab_test:
    st.sidebar.header("Thresholds (test tab only)")
    reject_t = st.sidebar.slider("Unsafe if explicit score >=", 0.0, 1.0, float(config.REJECT_THRESHOLD), 0.01)
    review_t = st.sidebar.slider("Uncertain if explicit score >=", 0.0, 1.0, float(config.REVIEW_THRESHOLD), 0.01)
    sugg_t = st.sidebar.slider("Uncertain if suggestive/nudity score >=", 0.0, 1.0,
                               float(config.SUGGESTIVE_THRESHOLD), 0.01)
    if review_t > reject_t:
        st.sidebar.warning("The uncertain threshold should not be higher than the unsafe threshold.")
    st.sidebar.caption("The live API always uses the thresholds saved in config.py.")
    st.sidebar.caption(f"Max file size: {config.MAX_IMAGE_BYTES // (1024 * 1024)} MB")

    st.write("Upload one or more images. The score and decision appear below.")
    with st.expander("How to read the score", expanded=False):
        st.markdown(
            f"""
Three models look at every image and two scores are produced (0 to 1):
- **Explicit score**: the highest NSFW signal among the three models.
- **Suggestive score**: nudity or suggestive content (the "sexy" class).

| Scores | Meaning | Decision |
|---|---|---|
| explicit **below {review_t:.2f}** and suggestive **below {sugg_t:.2f}** | Looks safe | ✅ Approve / publish |
| explicit **{review_t:.2f} to {reject_t:.2f}**, or suggestive **{sugg_t:.2f}+** | Uncertain: swimwear, shirtless, art, borderline | 🟡 Manual review (do not publish yet) |
| explicit **{reject_t:.2f} and above** | Explicit or nude content | ⛔ Reject, keep private |

Policy: nudity (including artistic) is rejected or sent to review, never auto-approved.
If an error occurs (corrupt file, timeout) keep the image **private**; never publish by default.
"""
        )

    files = st.file_uploader("Images (" + ", ".join(ALLOWED_TYPES) + ")", type=ALLOWED_TYPES,
                             accept_multiple_files=True)
    st.session_state.setdefault("cache", {})

    rows = []
    if files:
        mod = get_moderator()
        for idx, f in enumerate(files):
            data = f.getvalue()
            key = (f.name, len(data))
            if key not in st.session_state["cache"]:
                t0 = time.perf_counter()
                try:
                    with st.spinner(f"Checking {f.name} (the first request can take 10-15 seconds)..."):
                        res = mod.predict_bytes.remote(data)
                    res["round_trip_ms"] = round((time.perf_counter() - t0) * 1000)
                except Exception as e:  # invalid image, network, etc.
                    res = {"error": str(e)}
                st.session_state["cache"][key] = res
            res = st.session_state["cache"][key]

            c1, c2 = st.columns([1, 2])
            with c1:
                try:
                    st.image(Image.open(io.BytesIO(data)), caption=f.name, use_container_width=True)
                except Exception:
                    st.warning("Preview not available")
            with c2:
                st.subheader(f.name)
                truth = st.radio("Your own label (optional, used for the accuracy summary)",
                                 ["not set", "safe", "unsafe"], horizontal=True,
                                 key=f"label_{idx}_{f.name}_{len(data)}")
                if "error" in res:
                    st.error(f"Error: {res['error']} -> keep this image PRIVATE")
                    rows.append({"file": f.name, "nsfw": None, "suggestive": None,
                                 "decision": "error (keep private)", "your_label": truth, "ms": None})
                else:
                    nsfw = res["nsfw_score"]
                    sugg = res.get("suggestive_score", 0.0)
                    decision = config.decide(nsfw, sugg, reject_t, review_t, sugg_t)
                    label, color = BADGE[decision]
                    st.markdown(f"### :{color}[{label}]")
                    st.write(f"**Explicit score**: {nsfw:.4f}")
                    st.progress(min(max(nsfw, 0.0), 1.0))
                    st.write(f"**Suggestive / nudity score**: {sugg:.4f}")
                    st.progress(min(max(sugg, 0.0), 1.0))
                    if res.get("reasons"):
                        st.caption("Triggered by: " + ", ".join(res["reasons"]))
                    with st.expander("Per-model scores"):
                        st.json(res.get("details", {}))
                    st.caption(f"Model time: {res['latency_ms']} ms | Total incl. network: {res['round_trip_ms']} ms")
                    rows.append({"file": f.name, "nsfw": nsfw, "suggestive": sugg, "decision": decision,
                                 "your_label": truth, "ms": res["latency_ms"]})
            st.divider()

    if rows:
        st.subheader("Summary")
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True)
        st.download_button("Download CSV", df.to_csv(index=False).encode(), "results.csv", "text/csv")

        labelled = df[df["your_label"].isin(["safe", "unsafe"]) & df["nsfw"].notna()]
        if len(labelled):
            st.subheader("Accuracy on the images you labelled")
            safe = labelled[labelled["your_label"] == "safe"]
            unsafe = labelled[labelled["your_label"] == "unsafe"]
            false_pos = int((safe["decision"] != "approve").sum())
            false_neg = int((unsafe["decision"] == "approve").sum())
            m1, m2, m3 = st.columns(3)
            m1.metric("Labelled images", len(labelled))
            m2.metric("False positives (safe image flagged)", f"{false_pos} / {len(safe)}")
            m3.metric("False negatives (unsafe image approved)", f"{false_neg} / {len(unsafe)}")
            st.caption("Aim for zero false negatives first, then reduce false positives with the sliders. "
                       "Use at least 50-100 varied images before trusting the numbers.")

# =====================================================================================
# Tab 2: API access
# =====================================================================================
EXAMPLES = {
    "curl": ("bash", '''curl -X POST "__URL__" \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -F "file=@photo.jpg"'''),
    "JavaScript (browser / Node 18+)": ("javascript", '''const form = new FormData();
form.append("file", fileInput.files[0]);          // a File from <input type="file">

const res = await fetch("__URL__", {
  method: "POST",
  headers: { Authorization: "Bearer YOUR_API_KEY" },
  body: form,
});
if (!res.ok) {
  // 401 bad key, 400 bad image, 429 too many requests, 5xx server error
  // -> in ALL these cases keep the image private
}
const result = await res.json();
if (result.decision === "approve") { /* publish */ }
else if (result.decision === "review") { /* hold for manual review */ }
else { /* reject */ }'''),
    "Python": ("python", '''import requests

with open("photo.jpg", "rb") as f:
    r = requests.post(
        "__URL__",
        headers={"Authorization": "Bearer YOUR_API_KEY"},
        files={"file": f},
        timeout=60,   # the first request after idle can take ~15 s (cold start)
    )
r.raise_for_status()
result = r.json()
print(result["decision"], result["nsfw_score"])'''),
    "Android (Kotlin, OkHttp)": ("kotlin", '''val body = MultipartBody.Builder().setType(MultipartBody.FORM)
    .addFormDataPart("file", "photo.jpg", imageFile.asRequestBody("image/jpeg".toMediaType()))
    .build()
val request = Request.Builder()
    .url("__URL__")
    .header("Authorization", "Bearer YOUR_API_KEY")
    .post(body)
    .build()
OkHttpClient().newCall(request).execute().use { response ->
    val json = response.body?.string()   // parse "decision" from the JSON
}'''),
    "Flutter (Dart, http)": ("dart", '''final request = http.MultipartRequest('POST', Uri.parse('__URL__'))
  ..headers['Authorization'] = 'Bearer YOUR_API_KEY'
  ..files.add(await http.MultipartFile.fromPath('file', imagePath));
final response = await http.Response.fromStream(await request.send());
final result = jsonDecode(response.body);   // result['decision']'''),
    "iOS (Swift, URLSession)": ("swift", '''let boundary = UUID().uuidString
var req = URLRequest(url: URL(string: "__URL__")!)
req.httpMethod = "POST"
req.setValue("Bearer YOUR_API_KEY", forHTTPHeaderField: "Authorization")
req.setValue("multipart/form-data; boundary=\\(boundary)", forHTTPHeaderField: "Content-Type")
var body = Data()
body.append("--\\(boundary)\\r\\nContent-Disposition: form-data; name=\\"file\\"; filename=\\"photo.jpg\\"\\r\\nContent-Type: image/jpeg\\r\\n\\r\\n".data(using: .utf8)!)
body.append(imageData)
body.append("\\r\\n--\\(boundary)--\\r\\n".data(using: .utf8)!)
req.httpBody = body
let (data, _) = try await URLSession.shared.data(for: req)   // JSON with "decision"'''),
}

with tab_api:
    st.header("Use the moderation API in your website or app")
    st.markdown("**Endpoint**")
    st.code(f"POST {ENDPOINT}", language="text")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Requests per minute", config.DEFAULT_PER_MINUTE)
    c2.metric("Requests per day", config.DEFAULT_PER_DAY)
    c3.metric("Max image size", f"{config.MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    c4.metric("Typical speed", "~1-2 s")
    st.caption("Limits apply per API key (the daily limit resets at 00:00 UTC). The first request after a quiet "
               "period can take 10-15 seconds (cold start). Need more? Ask the admin for a custom limit.")

    st.subheader("1. Get your API key")
    store = get_store()
    st.session_state.setdefault("keys_made", 0)
    with st.form("make_key"):
        kname = st.text_input("Name for your key (your app or website, e.g. my-shop)")
        korigins = st.text_input("Allowed websites (optional, comma separated, e.g. https://myshop.com)",
                                 help="If set, browser requests from other websites are blocked. Does not apply "
                                      "to server-to-server calls.")
        make = st.form_submit_button("Generate my key")
    if make:
        import keys as keylib

        if st.session_state["keys_made"] >= 3:
            st.error("You have already created 3 keys in this session. Ask the admin for more.")
        else:
            try:
                new_key, rec = store.create(
                    kname, source="self_service",
                    origins=[o for o in korigins.split(",") if o.strip()],
                )
                st.session_state["keys_made"] += 1
                st.session_state["new_key"] = (new_key, rec["name"])
            except keylib.KeyError_ as e:
                st.error(str(e))
    if st.session_state.get("new_key"):
        k, kn = st.session_state["new_key"]
        st.success(f"Key created for **{kn}**. Copy it now: it is shown only once and cannot be recovered.")
        st.code(k, language="text")
        st.warning("Keep this key secret. Best practice: call the API from YOUR backend server. A key placed in "
                   "website JavaScript or inside a mobile app can be extracted by anyone. If it leaks, ask the "
                   "admin to revoke it and make a new one.")

    st.subheader("2. Send an image")
    lang = st.selectbox("Language", list(EXAMPLES))
    syntax, template = EXAMPLES[lang]
    st.code(template.replace("__URL__", ENDPOINT), language=syntax)
    st.caption("The Kotlin, Flutter and Swift snippets are standard patterns but were not compiled here; "
               "adapt them to your project.")

    st.subheader("3. Read the response")
    st.code('''{
  "decision": "reject",             // "approve" | "review" | "reject"
  "nsfw_score": 0.74,               // explicit content score, 0 to 1
  "suggestive_score": 0.99,         // nudity / suggestive score, 0 to 1
  "scores": {"normal": 0.26, "nsfw": 0.74},
  "details": {"falconsai": {...}, "marqo": {...}, "luke": {...}},   // per-model scores
  "reasons": ["marqo_nsfw=0.74", "luke_sexy=1.00"],
  "model": "ensemble(...)",
  "latency_ms": 1075.0
}''', language="json")
    st.markdown(
        """
| `decision` | What your app should do |
|---|---|
| `approve` | Safe to publish |
| `review` | Do **not** publish yet; send to a human moderator |
| `reject` | Do not publish; keep private or delete |

| HTTP status | Meaning | What to do |
|---|---|---|
| 200 | OK | Use `decision` |
| 400 | Empty, corrupt or too large image | Ask the user for another image |
| 401 | Missing, wrong or revoked key | Check your key |
| 403 | Key not allowed for this website | Check the allowed websites |
| 429 | Too many requests | Wait for `Retry-After` seconds |
| 5xx | Server problem | Retry later. **Keep the image private** |

**Golden rule:** if anything goes wrong (timeout, error, no answer), keep the image **private**. Never publish by default.

Other endpoints: `GET /v1/usage` (your limits and usage, needs your key), `GET /health`, and interactive docs at `/docs`.
"""
    )

    st.subheader("4. Try it live")
    st.caption("Uses your key against the real API, so it counts toward your limits.")
    tk = st.text_input("Your API key", type="password", key="try_key")
    tf = st.file_uploader("Image", type=ALLOWED_TYPES, key="try_file")
    cc1, cc2 = st.columns(2)
    if cc1.button("Send image to the API") and tk and tf:
        import httpx

        try:
            with st.spinner("Calling the API (the first call can take ~15 s)..."):
                r = httpx.post(ENDPOINT, headers={"Authorization": f"Bearer {tk.strip()}"},
                               files={"file": (tf.name, tf.getvalue())}, timeout=90)
            st.write(f"HTTP status: **{r.status_code}**")
            if r.headers.get("Retry-After"):
                st.write(f"Retry-After: {r.headers['Retry-After']} s")
            st.json(r.json())
        except Exception as e:
            st.error(f"Request failed: {e}  -> keep the image private")
    if cc2.button("Check my usage") and tk:
        import httpx

        try:
            r = httpx.get(f"{BASE_URL}/v1/usage", headers={"Authorization": f"Bearer {tk.strip()}"}, timeout=90)
            st.write(f"HTTP status: **{r.status_code}**")
            st.json(r.json())
        except Exception as e:
            st.error(f"Request failed: {e}")

# =====================================================================================
# Tab 3: about the models
# =====================================================================================
with tab_models:
    st.header("How the decision is made")
    st.markdown(
        """
Every image is checked by **three independent open-source models** (all Apache-2.0 licensed, no data is sent to
any outside AI service). Their outputs are combined, and the **most cautious answer wins**.

**Why three models?** Each model has blind spots, and one model's miss is often caught by another.
We saw this in a real test: a semi-nude artistic photo got an NSFW score of only **0.0005** from the first
model (a miss!), but **0.74** from the second and a **"sexy" score of 0.9998** from the third.
"""
    )

    st.subheader("The three models")
    st.markdown(
        """
| | **1. Falconsai** | **2. Marqo** | **3. LukeJacob** |
|---|---|---|---|
| **Hugging Face id** | `Falconsai/nsfw_image_detection` | `Marqo/nsfw-image-detection-384` | `LukeJacob2023/nsfw-image-detector` |
| **Type** | Vision Transformer (ViT-base), fine-tuned from `google/vit-base-patch16-224-in21k` | Tiny Vision Transformer (ViT-tiny, 384 px input) | Vision Transformer (ViT-base) |
| **Download size** | 327 MB | 22 MB | 343 MB |
| **Output classes** | `normal`, `nsfw` | `SFW`, `NSFW` | `drawings`, `hentai`, `neutral`, `porn`, `sexy` |
| **Training data** | ~80,000 private images (per its model card) | Author's own dataset; reports 98.56% accuracy on it | See its model card |
| **License** | Apache-2.0 | Apache-2.0 | Apache-2.0 |
| **Why we use it** | General-purpose explicit-content detector; a solid baseline | Very small and fast; good at partial nudity | Gives nuance instead of yes/no: the **"sexy"** class flags nudity and borderline images; separate classes for drawn/hentai content |
| **Known weakness** | Missed an artistic nude photo (score 0.0005) | Less detail than the larger models | Over-flags: a swimmer photographed from behind scored "sexy" 0.9998 |
"""
    )
    st.caption("Accuracy numbers quoted above are the model authors' own, measured on their own datasets, "
               "not on your images.")

    st.subheader("How the scores are combined")
    st.markdown(
        f"""
- **Explicit score** = the highest of: Falconsai *nsfw*, Marqo *NSFW*, LukeJacob *porn + hentai*.
- **Suggestive score** = LukeJacob *sexy*.

| Rule | Decision |
|---|---|
| explicit score ≥ **{config.REJECT_THRESHOLD:.2f}** | ⛔ `reject` |
| explicit score ≥ **{config.REVIEW_THRESHOLD:.2f}**, or suggestive score ≥ **{config.SUGGESTIVE_THRESHOLD:.2f}** | 🟡 `review` |
| otherwise | ✅ `approve` |

Policy: nudity, including artistic nudity, is never auto-approved.
"""
    )

    st.subheader("Honest limits")
    st.markdown(
        """
- These are statistical classifiers, **not guarantees**. Expect some **false positives** (safe images flagged,
  e.g. sports or beach photos go to review) and some **false negatives** (rare, but possible).
- The thresholds are starting values chosen from a small sample. Calibrate them on your own legal images
  (see `evaluate.py` in the repository) before relying on them.
- Always keep a human review path for the `review` decision, and keep images private when the API fails.
- Images are processed in memory and are not stored by this service.
"""
    )

# =====================================================================================
# Tab 4: admin
# =====================================================================================
with tab_admin:
    ADMIN_PW = os.environ.get("UI_ADMIN_PASSWORD", "")
    st.header("Admin")
    if not ADMIN_PW:
        st.info("Admin password is not configured.")
    else:
        apw = st.text_input("Admin password", type="password", key="admin_pw")
        if apw and hmac.compare_digest(apw, ADMIN_PW):
            store = get_store()
            import keys as keylib

            st.subheader("API keys")
            data = store.list()
            if data:
                st.dataframe(pd.DataFrame(data), use_container_width=True)
            else:
                st.write("No keys yet.")

            st.subheader("Revoke a key")
            active_names = [r["name"] for r in data if r["active"]]
            if active_names:
                with st.form("revoke"):
                    who = st.selectbox("Key name", active_names)
                    if st.form_submit_button("Revoke"):
                        n = store.revoke(who)
                        st.success(f"Revoked {n} key(s) named {who}. It stops working within ~30 seconds.")
            else:
                st.write("No active keys.")

            st.subheader("Create a key with custom limits")
            with st.form("admin_create"):
                an = st.text_input("Name")
                ap1, ap2 = st.columns(2)
                pm = ap1.number_input("Per minute", 1, 1000, config.DEFAULT_PER_MINUTE)
                pd_ = ap2.number_input("Per day", 1, 100000, config.DEFAULT_PER_DAY)
                ao = st.text_input("Allowed websites (optional, comma separated)")
                if st.form_submit_button("Create"):
                    try:
                        k, rec = store.create(an, source="admin", per_minute=int(pm), per_day=int(pd_),
                                              origins=[o for o in ao.split(",") if o.strip()])
                        st.success("Created. Copy it now (shown once):")
                        st.code(k, language="text")
                    except keylib.KeyError_ as e:
                        st.error(str(e))
        elif apw:
            st.error("Wrong admin password")

"""Streamlit test UI for the NSFW moderation service.

Runs on Modal (see modal_app.py) or locally:  streamlit run ui_app.py
It calls the deployed Modal class directly, so no API key is exposed to the browser.
"""
import hmac
import io
import os
import time

import pandas as pd
import streamlit as st
from PIL import Image

import config

st.set_page_config(page_title="Image Moderation Tester", page_icon="🛡️", layout="wide")

ALLOWED_TYPES = ["jpg", "jpeg", "jfif", "jpe", "png", "webp", "bmp", "gif", "tif", "tiff"]

# ---------- password gate ----------
PASSWORD = os.environ.get("UI_PASSWORD", "")
if PASSWORD:
    if not st.session_state.get("authed"):
        st.title("🛡️ Image Moderation Tester")
        pw = st.text_input("Password", type="password")
        if pw:
            if hmac.compare_digest(pw, PASSWORD):
                st.session_state["authed"] = True
                st.rerun()
            else:
                st.error("Wrong password")
        st.stop()


@st.cache_resource
def get_moderator():
    import modal

    return modal.Cls.from_name(config.APP_NAME, "Moderator")()


BADGE = {
    "approve": ("✅ SAFE (approve)", "green"),
    "review": ("🟡 UNCERTAIN (manual review)", "orange"),
    "reject": ("⛔ UNSAFE (reject)", "red"),
}

# ---------- sidebar ----------
st.sidebar.header("Thresholds")
reject_t = st.sidebar.slider("Unsafe if explicit score >=", 0.0, 1.0, float(config.REJECT_THRESHOLD), 0.01)
review_t = st.sidebar.slider("Uncertain if explicit score >=", 0.0, 1.0, float(config.REVIEW_THRESHOLD), 0.01)
sugg_t = st.sidebar.slider("Uncertain if suggestive/nudity score >=", 0.0, 1.0, float(config.SUGGESTIVE_THRESHOLD), 0.01)
if review_t > reject_t:
    st.sidebar.warning("The uncertain threshold should not be higher than the unsafe threshold.")
st.sidebar.caption("Models: Falconsai + Marqo + LukeJacob (ensemble, all Apache-2.0)")
st.sidebar.caption(f"Max file size: {config.MAX_IMAGE_BYTES // (1024 * 1024)} MB")

st.title("🛡️ Image Moderation Tester")
st.write("Upload one or more images. The model runs on Modal cloud and the score and decision appear below.")

# ---------- score guide ----------
with st.expander("How to read the score", expanded=True):
    st.markdown(
        f"""
Three models look at every image. Two scores are produced (0 to 1):
- **Explicit score**: the highest NSFW signal among the three models (porn / hentai / NSFW).
- **Suggestive score**: nudity or suggestive content (the "sexy" class).

| Scores | Meaning | Decision |
|---|---|---|
| explicit **below {review_t:.2f}** and suggestive **below {sugg_t:.2f}** | Looks safe | ✅ Approve / publish |
| explicit **{review_t:.2f} to {reject_t:.2f}**, or suggestive **{sugg_t:.2f}+** | Uncertain: swimwear, shirtless, art, borderline | 🟡 Manual review (do not publish yet) |
| explicit **{reject_t:.2f} and above** | Explicit or nude content | ⛔ Reject, keep private |

Notes:
- Policy: artistic nudity should be blocked, so nudity is rejected or sent to review, never auto-approved.
- These cut-offs are starting values from a very small sample. Tune them with the sliders and the labelling feature below.
- If an error occurs (corrupt file, timeout), keep the image **private**; never publish by default.
"""
    )

files = st.file_uploader(
    "Images (" + ", ".join(ALLOWED_TYPES) + ")", type=ALLOWED_TYPES, accept_multiple_files=True
)

if "cache" not in st.session_state:
    st.session_state["cache"] = {}  # (name, size) -> result

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
            truth = st.radio(
                "Your own label (optional, used for the accuracy summary)",
                ["not set", "safe", "unsafe"],
                horizontal=True,
                key=f"label_{idx}_{f.name}_{len(data)}",
            )
            if "error" in res:
                st.error(f"Error: {res['error']} -> keep this image PRIVATE")
                rows.append(
                    {"file": f.name, "nsfw": None, "suggestive": None, "decision": "error (keep private)",
                     "your_label": truth, "ms": None}
                )
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
                rows.append(
                    {"file": f.name, "nsfw": nsfw, "suggestive": sugg, "decision": decision,
                     "your_label": truth, "ms": res["latency_ms"]}
                )
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
        false_pos = int((safe["decision"] != "approve").sum())  # safe image not approved
        false_neg = int((unsafe["decision"] == "approve").sum())  # unsafe image approved (worst case)
        m1, m2, m3 = st.columns(3)
        m1.metric("Labelled images", len(labelled))
        m2.metric("False positives (safe image blocked/flagged)", f"{false_pos} / {len(safe)}")
        m3.metric("False negatives (unsafe image approved)", f"{false_neg} / {len(unsafe)}")
        st.caption(
            "Aim for zero false negatives first, then reduce false positives by moving the sliders. "
            "Use at least 50-100 varied images before trusting the numbers."
        )

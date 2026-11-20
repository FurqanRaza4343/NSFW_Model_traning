"""Central configuration for the moderation service."""
import os

# --- Ensemble of 3 public models (all Apache-2.0) ---
FALCONSAI_ID = "Falconsai/nsfw_image_detection"      # ViT-base, normal / nsfw
MARQO_ID = "Marqo/nsfw-image-detection-384"          # tiny ViT (timm), NSFW / SFW
LUKE_ID = "LukeJacob2023/nsfw-image-detector"        # ViT-base, 5 classes
MODEL_ID = f"ensemble({FALCONSAI_ID}, {MARQO_ID}, {LUKE_ID})"
MODEL_REVISION = None  # optionally pin commit hashes for reproducibility

API_VERSION = "1.1.0"
APP_NAME = "image-moderation"
VOLUME_NAME = "nsfw-model-cache"
SECRET_NAME = "moderation-auth"

# Inside Modal: /cache (Volume). Locally: set LOCAL_CACHE_DIR (see local_test.py).
CACHE_DIR = os.environ.get("LOCAL_CACHE_DIR", "/cache")

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_IMAGE_PIXELS = 25_000_000
REQUEST_TIMEOUT_S = 60

# --- Customer API keys ---
KEYS_DICT = "moderation-keys"        # modal.Dict: sha256(key) -> record
USAGE_DICT = "moderation-usage"      # modal.Dict: sha256(key) -> counters
DEFAULT_PER_MINUTE = 20              # requests per minute per customer key
DEFAULT_PER_DAY = 200                # requests per day (UTC) per customer key
MAX_SELF_SERVICE_KEYS = 1000         # cap on keys created from the UI (each key is trial-limited)

# --- Lead capture and free trial ---
LEADS_DICT = "moderation-leads"       # modal.Dict: sha256(email) -> lead record (name, email, phone, trial)
TRIAL_CHECKS = 2                     # free image checks per registered person
GLOBAL_TRIAL_PER_DAY = 300           # free checks per day across everyone (protects the free credits)
REQUIRE_PHONE = False                # set True to make the phone number mandatory
REQUIRE_CONSENT = False              # True: registration shows a consent checkbox (and enables image retention)
SHOW_PRIVACY_NOTICE = False          # True: show the privacy note in the UI

# --- Image retention (only for people who agreed; see retention.py) ---
STORE_IMAGES = True                  # master switch. Stored only if the Apps Script has a Drive folder set
STORE_DECISIONS = ("approve", "review")   # REJECTED images are never stored, only scores + SHA-256
IMAGE_RETENTION_DAYS = 30            # the Apps Script deletes stored images after this many days


# --- Decision policy (PROVISIONAL - calibrate on your own labelled images) ---
# nsfw_score = max(Falconsai nsfw, Marqo NSFW, Luke porn+hentai)   -> explicit content
# suggestive_score = Luke "adult"                                    -> nudity / suggestive / borderline
REJECT_THRESHOLD = 0.50       # nsfw_score >= this  -> reject
REVIEW_THRESHOLD = 0.30       # nsfw_score >= this  -> manual review
SUGGESTIVE_THRESHOLD = 0.50   # suggestive_score >= this -> manual review


def decide(nsfw: float, suggestive: float,
           reject_t: float = REJECT_THRESHOLD,
           review_t: float = REVIEW_THRESHOLD,
           suggestive_t: float = SUGGESTIVE_THRESHOLD) -> str:
    if nsfw >= reject_t:
        return "reject"
    if nsfw >= review_t or suggestive >= suggestive_t:
        return "review"
    return "approve"

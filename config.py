"""Central configuration for the moderation service."""
import os

# --- Ensemble of 3 public models (all Apache-2.0) ---
FALCONSAI_ID = "Falconsai/nsfw_image_detection"      # ViT-base, normal / nsfw
MARQO_ID = "Marqo/nsfw-image-detection-384"          # tiny ViT (timm), NSFW / SFW
LUKE_ID = "LukeJacob2023/nsfw-image-detector"        # ViT-base, 5 classes
MODEL_ID = f"ensemble({FALCONSAI_ID}, {MARQO_ID}, {LUKE_ID})"
MODEL_REVISION = None  # optionally pin commit hashes for reproducibility

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
MAX_SELF_SERVICE_KEYS = 20           # cap on keys created from the UI

# --- Decision policy (PROVISIONAL - calibrate on your own labelled images) ---
# nsfw_score = max(Falconsai nsfw, Marqo NSFW, Luke porn+hentai)   -> explicit content
# suggestive_score = Luke "sexy"                                    -> nudity / suggestive / borderline
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

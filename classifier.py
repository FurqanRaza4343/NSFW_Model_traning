"""Ensemble inference (3 models). Loaded once per container."""
import io
import json
import time

import torch
from huggingface_hub import hf_hub_download
from PIL import Image, UnidentifiedImageError
from transformers import AutoImageProcessor, AutoModelForImageClassification, ViTImageProcessor

import config


class ImageError(ValueError):
    pass


def _hf_kwargs():
    kw = {"cache_dir": config.CACHE_DIR}
    if config.MODEL_REVISION:
        kw["revision"] = config.MODEL_REVISION
    return kw


class NSFWClassifier:
    def __init__(self):
        import timm

        kw = _hf_kwargs()
        # 1) Falconsai
        self.f_proc = AutoImageProcessor.from_pretrained(config.FALCONSAI_ID, **kw)
        self.f_model = AutoModelForImageClassification.from_pretrained(config.FALCONSAI_ID, **kw).eval()
        # 2) LukeJacob (its preprocessor_config has no processor type, so build it by hand)
        cfg_path = hf_hub_download(config.LUKE_ID, "preprocessor_config.json", cache_dir=config.CACHE_DIR)
        cfg = {k: v for k, v in json.load(open(cfg_path)).items()
               if k not in ("feature_extractor_type", "image_processor_type")}
        self.l_proc = ViTImageProcessor(**cfg)
        self.l_model = AutoModelForImageClassification.from_pretrained(config.LUKE_ID, **kw).eval()
        # 3) Marqo (timm)
        self.m_model = timm.create_model("hf_hub:" + config.MARQO_ID, pretrained=True).eval()
        self.m_tf = timm.data.create_transform(**timm.data.resolve_data_config({}, model=self.m_model))
        self.m_labels = self.m_model.pretrained_cfg["label_names"]  # ['NSFW', 'SFW']

    @staticmethod
    def load_image(data: bytes) -> Image.Image:
        if not data:
            raise ImageError("Empty file")
        if len(data) > config.MAX_IMAGE_BYTES:
            raise ImageError("Image too large")
        try:
            img = Image.open(io.BytesIO(data))
            img.verify()  # detect corrupt files
            img = Image.open(io.BytesIO(data))  # reopen after verify
            if img.width * img.height > config.MAX_IMAGE_PIXELS:
                raise ImageError("Image dimensions too large")
            return img.convert("RGB")
        except (UnidentifiedImageError, OSError, SyntaxError):
            raise ImageError("Invalid or corrupt image")

    @staticmethod
    def _hf_probs(proc, model, img):
        with torch.no_grad():
            p = torch.softmax(model(**proc(images=img, return_tensors="pt")).logits, -1)[0].tolist()
        return {model.config.id2label[i].lower(): round(v, 6) for i, v in enumerate(p)}

    def predict(self, data: bytes) -> dict:
        start = time.perf_counter()
        img = self.load_image(data)

        falcons = self._hf_probs(self.f_proc, self.f_model, img)
        luke = self._hf_probs(self.l_proc, self.l_model, img)
        # The model's own label for this class is "sexy"; this service reports it as "adult".
        luke = {("adult" if k == "sexy" else k): v for k, v in luke.items()}
        with torch.no_grad():
            mp = torch.softmax(self.m_model(self.m_tf(img).unsqueeze(0)), -1)[0].tolist()
        marqo = {self.m_labels[i].lower(): round(v, 6) for i, v in enumerate(mp)}

        explicit = {
            "falconsai_nsfw": falcons.get("nsfw", 0.0),
            "marqo_nsfw": marqo.get("nsfw", 0.0),
            "luke_porn_hentai": round(luke.get("porn", 0.0) + luke.get("hentai", 0.0), 6),
        }
        nsfw = max(explicit.values())
        suggestive = luke.get("adult", 0.0)
        decision = config.decide(nsfw, suggestive)

        reasons = [f"{k}={v:.2f}" for k, v in explicit.items() if v >= config.REVIEW_THRESHOLD]
        if suggestive >= config.SUGGESTIVE_THRESHOLD:
            reasons.append(f"luke_adult={suggestive:.2f}")

        return {
            "decision": decision,
            "nsfw_score": round(nsfw, 6),
            "suggestive_score": round(suggestive, 6),
            "scores": {"normal": round(1 - nsfw, 6), "nsfw": round(nsfw, 6)},
            "details": {"falconsai": falcons, "marqo": marqo, "luke": luke},
            "reasons": reasons,
            "model": config.MODEL_ID,
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        }

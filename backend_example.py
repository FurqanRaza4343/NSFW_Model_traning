"""Example FastAPI backend wired to the moderation service.

Run:  uvicorn backend_example:app --reload
Needs MODERATION_URL and MODERATION_API_KEY in the environment (or .env).

Copy the pattern into your real backend: the upload route calls the moderation
service, and the BACKEND (not the frontend) decides what happens to the image.
"""
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile

from backend_client import ModerationClient

load_dotenv()
app = FastAPI(title="Example backend")
moderation = ModerationClient()

MAX_BYTES = 5 * 1024 * 1024


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    data = await file.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(400, "Empty or larger than 5 MB")

    result = await moderation.check(data, file.filename or "image")

    # TODO in your real backend: save `data` to storage with visibility based on result.status
    #   publish -> public, pending_review -> hidden + review queue, rejected / private -> hidden
    return {
        "status": result.status,
        "published": result.status == "publish",
        "reasons": result.reasons,
        "error": result.error,  # log this server-side; you may hide it from end users
    }

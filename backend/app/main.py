from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api import meetings, webhooks

app = FastAPI(title="Meeting Recorder API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meetings.router)
app.include_router(webhooks.router)


@app.get("/health")
def health():
    return {"status": "ok"}

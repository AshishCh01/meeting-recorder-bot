from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api import chat, meetings, webhooks

app = FastAPI(title="Meeting Recorder API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_origin, 
        "http://localhost:5173",  # <-- Fixed port here!
        "http://127.0.0.1:5173"   # <-- Good to include just in case
    ],
    allow_credentials=True,       # <-- Added this for auth compatibility
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meetings.router)
app.include_router(webhooks.router)
app.include_router(chat.router)


@app.get("/health")
def health():
    return {"status": "ok"}
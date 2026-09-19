from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import User
from app.api.auth import get_current_user
from app.models.meeting import UserSettings, UserSettingsUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserSettings)
def get_my_settings(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    return _settings_out(user)


def _settings_out(user: User) -> UserSettings:
    return UserSettings(
        id=str(user.id),
        email=user.email,
        bot_display_name=user.bot_display_name,
        ask_ai_instructions=user.ask_ai_instructions,
    )


@router.patch("/me", response_model=UserSettings)
def update_my_settings(
    payload: UserSettingsUpdate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")

    # Only what the body actually contains - a field left out is untouched.
    sent = payload.model_fields_set
    if "bot_display_name" in sent:
        if payload.bot_display_name is None:
            raise HTTPException(422, "bot_display_name cannot be null")
        user.bot_display_name = payload.bot_display_name.strip()
    if "ask_ai_instructions" in sent:
        user.ask_ai_instructions = (payload.ask_ai_instructions or "").strip() or None
    db.commit()
    db.refresh(user)
    return _settings_out(user)

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
    return UserSettings(id=str(user.id), email=user.email, bot_display_name=user.bot_display_name)


@router.patch("/me", response_model=UserSettings)
def update_my_settings(
    payload: UserSettingsUpdate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")

    user.bot_display_name = payload.bot_display_name.strip()
    db.commit()
    db.refresh(user)
    return UserSettings(id=str(user.id), email=user.email, bot_display_name=user.bot_display_name)

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.db.supabase import supabase
from app.db.database import get_db
from app.db.models import User
from app.config import settings

security = HTTPBearer()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> str:
    token = credentials.credentials
    try:
        auth_response = supabase.auth.get_user(token)
        user_data = auth_response.user
        if not user_data:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Could not validate credentials: {str(e)}")

    user_id = user_data.id
    email = user_data.email or ""

    # Ensure user exists in our local DB mirror
    db_user = db.query(User).filter(User.id == user_id).first()
    if not db_user:
        db_user = User(id=user_id, email=email)
        db.add(db_user)
        db.commit()
    
    return user_id

def verify_webhook_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    token = credentials.credentials
    if token != settings.meeting_bot_bearer_token:
        raise HTTPException(status_code=401, detail="Invalid webhook token")

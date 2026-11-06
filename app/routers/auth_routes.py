from typing import List, Optional
from fastapi import APIRouter, Depends, Response, Request, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, TeamMember
from app.schemas import UserResponse, LoginRequest
from app.utils.security import verify_password

router = APIRouter(prefix="/api/auth", tags=["Autenticación y Sesión"])

@router.post("/login")
def login(req: LoginRequest, response: Response, db: Session = Depends(get_db)):
    """Inicia sesión con nombre de usuario y contraseña."""
    username_clean = req.username.strip().lower()
    user = db.query(User).filter(User.username == username_clean).first()

    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nombre de usuario o contraseña incorrectos"
        )

    # Establecer cookie de sesión
    response.set_cookie(
        key="noti_session_user_id",
        value=str(user.id),
        httponly=True,
        samesite="lax",
        path="/",
        max_age=86400 * 30 # 30 días
    )

    memberships = db.query(TeamMember).filter(TeamMember.user_id == user.id).all()
    roles_map = {m.team_id: m.role for m in memberships}

    return {
        "success": True,
        "user": {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "email": user.email,
            "avatar_url": user.avatar_url,
            "roles_by_team": roles_map
        }
    }

@router.post("/logout")
def logout(response: Response):
    """Cierra la sesión del usuario actual."""
    response.delete_cookie(key="noti_session_user_id", path="/")
    return {"success": True, "message": "Sesión cerrada correctamente"}

@router.get("/current-user")
def get_current_user(request: Request, db: Session = Depends(get_db)):
    """Retorna los datos del usuario autenticado actual."""
    user_id = request.cookies.get("noti_session_user_id") or request.headers.get("X-User-Id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado")

    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")

        memberships = db.query(TeamMember).filter(TeamMember.user_id == user.id).all()
        roles_map = {m.team_id: m.role for m in memberships}

        return {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "email": user.email,
            "avatar_url": user.avatar_url,
            "roles_by_team": roles_map
        }
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Identificador de sesión inválido")

@router.get("/users", response_model=List[UserResponse])
def list_available_users(db: Session = Depends(get_db)):
    """Lista usuarios registrados para selector de miembros en equipos."""
    return db.query(User).all()

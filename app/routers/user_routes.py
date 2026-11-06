from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, TeamMember
from app.schemas import UserCreate, UserUpdate, UserResponse
from app.utils.security import hash_password

router = APIRouter(prefix="/api/users", tags=["Gestión de Usuarios"])

def get_current_admin(request: Request, db: Session = Depends(get_db)) -> User:
    """Verifica que el usuario actual tenga rol de Administrador en algún equipo."""
    user_id = request.cookies.get("noti_session_user_id") or request.headers.get("X-User-Id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado")

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")

    # Verificar si es admin en algún equipo o si es el usuario admin inicial
    is_admin = (user.username == "admin") or db.query(TeamMember).filter(
        TeamMember.user_id == user.id,
        TeamMember.role == "ADMIN"
    ).first() is not None

    if not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permiso denegado: Se requiere rol de Administrador")
    return user

@router.get("")
def list_users(db: Session = Depends(get_db)):
    """Lista todos los usuarios con sus roles y equipos asociados."""
    users = db.query(User).all()
    results = []
    for u in users:
        memberships = db.query(TeamMember).filter(TeamMember.user_id == u.id).all()
        teams_info = [{"team_id": m.team_id, "team_name": m.team.name, "role": m.role} for m in memberships]
        results.append({
            "id": u.id,
            "username": u.username,
            "name": u.name,
            "email": u.email,
            "avatar_url": u.avatar_url,
            "teams": teams_info
        })
    return results

@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(user_in: UserCreate, db: Session = Depends(get_db)):
    """Crea un nuevo usuario con credenciales de acceso."""
    username_clean = user_in.username.strip().lower()
    existing_username = db.query(User).filter(User.username == username_clean).first()
    if existing_username:
        raise HTTPException(status_code=400, detail="El nombre de usuario ya está en uso")

    existing_email = db.query(User).filter(User.email == user_in.email.strip().lower()).first()
    if existing_email:
        raise HTTPException(status_code=400, detail="El correo electrónico ya está registrado")

    user = User(
        username=username_clean,
        name=user_in.name.strip(),
        email=user_in.email.strip().lower(),
        password_hash=hash_password(user_in.password),
        avatar_url=user_in.avatar_url or f"https://api.dicebear.com/7.x/avataaars/svg?seed={username_clean}"
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@router.put("/{user_id}", response_model=UserResponse)
def update_user(user_id: int, user_in: UserUpdate, db: Session = Depends(get_db)):
    """Actualiza datos del usuario o restablece su contraseña."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user_in.name is not None:
        user.name = user_in.name.strip()
    if user_in.email is not None:
        user.email = user_in.email.strip().lower()
    if user_in.avatar_url is not None:
        user.avatar_url = user_in.avatar_url
    if user_in.password and len(user_in.password.strip()) >= 4:
        user.password_hash = hash_password(user_in.password.strip())

    db.commit()
    db.refresh(user)
    return user

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    """Elimina un usuario del sistema."""
    current_uid = request.cookies.get("noti_session_user_id")
    if current_uid and int(current_uid) == user_id:
        raise HTTPException(status_code=400, detail="No puedes eliminar tu propio usuario en sesión")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    db.delete(user)
    db.commit()
    return None

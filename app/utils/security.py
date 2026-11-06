import hashlib
import os

def hash_password(password: str) -> str:
    """Genera un hash seguro con salt para almacenar la contraseña."""
    salt = os.urandom(16).hex()
    hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}${hashed}"

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica si la contraseña ingresada coincide con el hash almacenado."""
    try:
        salt, expected_hash = hashed_password.split("$")
        computed_hash = hashlib.sha256((salt + plain_password).encode("utf-8")).hexdigest()
        return computed_hash == expected_hash
    except Exception:
        return False

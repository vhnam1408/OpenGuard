from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError


password_hasher = PasswordHasher(type=Type.ID, time_cost=3, memory_cost=65536, parallelism=4)


def hash_password(password: str) -> str:
    if not 10 <= len(password) <= 1024:
        raise ValueError("Password must contain 10 to 1024 characters")
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False

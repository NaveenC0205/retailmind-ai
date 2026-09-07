"""Authentication module with password hashing and JWT tokens."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, new_id


SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "retailmind-dev-secret-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


class TokenData(BaseModel):
    customer_id: str
    email: str
    is_admin: bool = False


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    customer_id: str
    name: str
    email: str
    is_admin: bool = False


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8")
        )
    except Exception:
        return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[TokenData]:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        customer_id = payload.get("sub")
        email = payload.get("email")
        is_admin = payload.get("is_admin", False)
        if customer_id is None or email is None:
            return None
        return TokenData(customer_id=customer_id, email=email, is_admin=is_admin)
    except JWTError:
        return None


async def get_customer_by_email(session: AsyncSession, email: str) -> Optional[Customer]:
    """Get a customer by email."""
    result = await session.execute(select(Customer).where(Customer.email == email))
    return result.scalar_one_or_none()


async def get_customer_by_id(session: AsyncSession, customer_id: str) -> Optional[Customer]:
    """Get a customer by ID."""
    return await session.get(Customer, customer_id)


async def register_customer(
    session: AsyncSession,
    email: str,
    password: str,
    name: str,
    is_admin: bool = False,
) -> Customer:
    """Register a new customer."""
    existing = await get_customer_by_email(session, email)
    if existing:
        raise ValueError("Email already registered")
    
    customer = Customer(
        id=new_id("CU"),
        email=email.lower().strip(),
        name=name.strip(),
        password_hash=hash_password(password),
        is_admin=is_admin,
        tier="standard",
        region="IN-KA",
    )
    session.add(customer)
    await session.commit()
    await session.refresh(customer)
    return customer


async def authenticate_customer(
    session: AsyncSession,
    email: str,
    password: str,
) -> Optional[Customer]:
    """Authenticate a customer by email and password."""
    customer = await get_customer_by_email(session, email.lower().strip())
    if not customer:
        return None
    if not customer.password_hash:
        return None
    if not verify_password(password, customer.password_hash):
        return None
    return customer


def create_token_for_customer(customer: Customer) -> Token:
    """Create a JWT token for an authenticated customer."""
    access_token = create_access_token({
        "sub": customer.id,
        "email": customer.email,
        "is_admin": customer.is_admin,
    })
    return Token(
        access_token=access_token,
        customer_id=customer.id,
        name=customer.name,
        email=customer.email,
        is_admin=customer.is_admin,
    )

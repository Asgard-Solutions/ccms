"""
Identity Service — User domain model.

Future relational schema:
  users (
    id           UUID PRIMARY KEY,
    email        VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    name         VARCHAR(200) NOT NULL,
    role         VARCHAR(20)  NOT NULL,  -- admin|doctor|staff|patient
    phone        VARCHAR(32),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
  );
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, EmailStr, Field, ConfigDict

Role = Literal["admin", "doctor", "staff", "patient"]


class UserPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    email: EmailStr
    name: str
    role: Role
    phone: str | None = None
    created_at: datetime


class UserRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = None


class AdminUserCreate(BaseModel):
    """Admin-only: can assign any role."""
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = None
    role: Role = "staff"


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6, max_length=128)

"""Users for "usage per person": a simple switcher, not a login. List users, add one, and look one up
by name when a question arrives."""

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import get_conn

router = APIRouter(prefix="/users")

NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


class NewUser(BaseModel):
    name: str


def user_id(name: str) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE name = %s", (name,)).fetchone()
    if row is None:
        raise HTTPException(400, f"Unknown user '{name}'. Pick one from GET /users or add one with POST /users.")
    return row[0]


@router.get("")
def list_users():
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name FROM users ORDER BY id").fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]


@router.post("", status_code=201)
def add_user(req: NewUser):
    if not NAME.match(req.name):
        raise HTTPException(400, "A user name uses letters, digits, '.', '-' or '_' (max 40).")
    with get_conn() as conn:
        conn.execute("INSERT INTO users (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (req.name,))
    return {"id": user_id(req.name), "name": req.name}

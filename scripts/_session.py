"""Phase 8: a signed-in test account for the Python verification scripts (model_matrix, load_test).

It registers a fresh account through the real /auth/register (random password, never printed or stored), keeps
the session cookie in the httpx client and sends the CSRF token on every request, like the browser app does.
The account's personal workspace and its first project hold whatever the script adds."""

import secrets
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class TestAccount:
    client: httpx.Client
    user_id: int
    email: str
    name: str
    org_id: int
    project_id: int
    password: str = field(default="", repr=False)  # kept in memory only (to sign in again); never printed or saved


def sign_up(api: str, label: str, timeout: float = 900) -> TestAccount:
    client = httpx.Client(base_url=api, timeout=timeout)
    email = f"verify-{label}-{int(time.time() * 1000)}-{secrets.token_hex(3)}@example.test"
    password = "V" + secrets.token_urlsafe(18) + "7"
    r = client.post("/auth/register", json={"full_name": f"Verify {label}", "email": email,
                                            "password": password, "confirm_password": password})
    r.raise_for_status()
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    me = client.get("/auth/me").json()
    org_id = next(w["id"] for w in me["workspaces"] if w["kind"] == "personal")
    project_id = client.get("/projects", params={"org_id": org_id}).json()[0]["id"]
    return TestAccount(client, me["user"]["id"], email, f"Verify {label}", org_id, project_id, password)


def add_file(acct: TestAccount, path, name: str | None = None, project_id: int | None = None) -> int:
    with open(path, "rb") as f:
        r = acct.client.post(f"/projects/{project_id or acct.project_id}/sources/file",
                             files={"file": (name or path.name, f, "application/pdf")})
    r.raise_for_status()
    return r.json()["id"]


def add_link(acct: TestAccount, kind: str, url: str, project_id: int | None = None) -> int:
    r = acct.client.post(f"/projects/{project_id or acct.project_id}/sources/{kind}", json={"url": url})
    r.raise_for_status()
    return r.json()["id"]


def sign_in(api: str, acct: TestAccount, timeout: float = 900) -> httpx.Client:
    """A brand-new client (no cookies) that signs in again with the account's password."""
    client = httpx.Client(base_url=api, timeout=timeout)
    r = client.post("/auth/login", json={"email": acct.email, "password": acct.password})
    r.raise_for_status()
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return client

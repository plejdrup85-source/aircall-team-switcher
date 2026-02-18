import os
import logging
from typing import Optional, Tuple

import requests
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets

# ----------------------------
# Config
# ----------------------------
AIRCALL_API_ID = os.getenv("AIRCALL_API_ID", "").strip()
AIRCALL_API_TOKEN = os.getenv("AIRCALL_API_TOKEN", "").strip()

TEAM_CS_ID = os.getenv("TEAM_CS_ID", "").strip()       # Kundeservice team ID
TEAM_TECH_ID = os.getenv("TEAM_TECH_ID", "").strip()   # Teknisk team ID
NINA_USER_ID = os.getenv("NINA_USER_ID", "").strip()   # Nina user ID (Aircall)

# Basic auth to protect the app
APP_BASIC_USER = os.getenv("APP_BASIC_USER", "admin").strip()
APP_BASIC_PASS = os.getenv("APP_BASIC_PASS", "").strip()

AIRCALL_BASE = "https://api.aircall.io/v1"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("switcher")

app = FastAPI(title="Aircall Team Switcher")
templates = Jinja2Templates(directory="templates")
security = HTTPBasic()


def must_env(name: str, value: str):
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")


@app.on_event("startup")
def startup_check():
    # Fail fast if config missing
    must_env("AIRCALL_API_ID", AIRCALL_API_ID)
    must_env("AIRCALL_API_TOKEN", AIRCALL_API_TOKEN)
    must_env("TEAM_CS_ID", TEAM_CS_ID)
    must_env("TEAM_TECH_ID", TEAM_TECH_ID)
    must_env("NINA_USER_ID", NINA_USER_ID)
    must_env("APP_BASIC_PASS", APP_BASIC_PASS)


def require_basic_auth(creds: HTTPBasicCredentials = Depends(security)) -> None:
    ok_user = secrets.compare_digest(creds.username, APP_BASIC_USER)
    ok_pass = secrets.compare_digest(creds.password, APP_BASIC_PASS)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})


def aircall_request(method: str, path: str) -> Tuple[int, str]:
    """
    Returns (status_code, response_text). Raises on network errors.
    """
    url = f"{AIRCALL_BASE}{path}"
    r = requests.request(
        method,
        url,
        auth=(AIRCALL_API_ID, AIRCALL_API_TOKEN),
        timeout=20,
    )
    return r.status_code, r.text


def aircall_add_user_to_team(team_id: str, user_id: str) -> None:
    # POST /teams/:team_id/users/:user_id
    status, text = aircall_request("POST", f"/teams/{team_id}/users/{user_id}")

    # Common “OK-ish” statuses (Aircall can vary)
    if status in (200, 201, 204):
        return

    # If already a member, some APIs return 409
    if status == 409:
        logger.info("User already in team (team_id=%s user_id=%s)", team_id, user_id)
        return

    raise HTTPException(status_code=500, detail=f"Aircall add failed: {status} {text}")


def aircall_remove_user_from_team(team_id: str, user_id: str) -> None:
    # DELETE /teams/:team_id/users/:user_id
    status, text = aircall_request("DELETE", f"/teams/{team_id}/users/{user_id}")

    if status in (200, 204):
        return

    # If not in team, some APIs return 404
    if status == 404:
        logger.info("User not in team (team_id=%s user_id=%s)", team_id, user_id)
        return

    raise HTTPException(status_code=500, detail=f"Aircall remove failed: {status} {text}")


def do_switch(mode: str) -> None:
    """
    mode: 'kundeservice' or 'teknisk'
    """
    if mode == "kundeservice":
        # add CS, remove TECH
        aircall_add_user_to_team(TEAM_CS_ID, NINA_USER_ID)
        aircall_remove_user_from_team(TEAM_TECH_ID, NINA_USER_ID)
        return

    if mode == "teknisk":
        # add TECH, remove CS
        aircall_add_user_to_team(TEAM_TECH_ID, NINA_USER_ID)
        aircall_remove_user_from_team(TEAM_CS_ID, NINA_USER_ID)
        return

    raise HTTPException(status_code=400, detail="Invalid mode")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, _=Depends(require_basic_auth)):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "cs_team": TEAM_CS_ID,
            "tech_team": TEAM_TECH_ID,
            "nina_user": NINA_USER_ID,
        },
    )


@app.post("/switch/{mode}")
def switch(mode: str, _=Depends(require_basic_auth)):
    try:
        do_switch(mode)
        logger.info("Switched OK: mode=%s user_id=%s", mode, NINA_USER_ID)
        return JSONResponse({"ok": True, "mode": mode})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Switch failed")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


@app.get("/health")
def health():
    return {"ok": True}

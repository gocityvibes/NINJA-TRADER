from __future__ import annotations
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pathlib import Path
from .routes import router
from . import db
from .state import STORE
from .config import BOT_MODE, KILL_SWITCH

app = FastAPI(title="Render Brain (Ninja Candles)", version="1.0")


@app.on_event("startup")
def _startup():
    db.init_db()
    STORE.set_mode(BOT_MODE)
    STORE.set_kill(KILL_SWITCH)


app.include_router(router)

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse("<h3>Render Brain running</h3><p>Use /status, /poll, /fingerprints</p>")

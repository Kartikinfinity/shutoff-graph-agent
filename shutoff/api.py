"""HTTP API + static UI.

    uvicorn shutoff.api:app --port 8000
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, db as dbm, network as N, seed

WEB = Path(__file__).resolve().parent.parent / "web"



def _ensure_seeded() -> None:
    graphs = dbm.db().list_graphs()
    for g in graphs:  # sandboxes left behind by a killed process
        if g.startswith(f"{dbm.GRAPH}__sim_"):
            dbm.graph(g).delete()
    if dbm.GRAPH not in graphs:
        seed.load()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _ensure_seeded()
    yield


app = FastAPI(title="ShutOff", version="1.0", lifespan=lifespan)


class ChatIn(BaseModel):
    session: str = Field(default="demo", max_length=40)
    text: str = Field(max_length=500)


class SessionIn(BaseModel):
    session: str = Field(default="demo", max_length=40)


class ApproveIn(SessionIn):
    acknowledge: list[str] = Field(default_factory=list, max_length=10)


class RestoreIn(SessionIn):
    work_order: str = Field(max_length=10)


@app.get("/api/health")
def health() -> dict:
    try:
        n = dbm.run("health", "MATCH (n) RETURN count(n) AS n")[0]["n"]
        ok = True
    except Exception as e:  # report, do not crash the UI
        n, ok = str(e), False
    return {"falkordb": ok, "graph": dbm.GRAPH, "nodes": n,
            "mode": "llm" if agent.llm_available() else "offline", "model": agent.MODEL if agent.llm_available() else None}


@app.get("/api/network")
def network() -> dict:
    return N.snapshot()


@app.post("/api/chat")
def chat(body: ChatIn) -> dict:
    if not body.text.strip():
        raise HTTPException(400, "empty message")
    return agent.handle(body.session, body.text)


@app.post("/api/approve")
def approve(body: ApproveIn) -> dict:
    return agent.approve(body.session, body.acknowledge)


@app.post("/api/restore")
def restore(body: RestoreIn) -> dict:
    return agent.restore(body.session, body.work_order)


@app.post("/api/reset")
def reset() -> dict:
    agent.SESSIONS.clear()
    return {"counts": seed.load()}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")))

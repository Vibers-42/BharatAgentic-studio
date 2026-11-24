"""FastAPI entry point: connects to the database on startup and mounts the route files.
Run locally with: uvicorn app.main:app --reload"""

from contextlib import asynccontextmanager

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from .db import close_db, init_db
from .auth import current_user
from .routes import auth, conversations, graphs, health, models, orgs, pipeline, projects, sources, tools, usage


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    close_db()


app = FastAPI(title="BharatAgent Studio API", lifespan=lifespan)

# The web app reaches the api through its own origin (nginx proxies /api), so cookies are same-origin. CORS is
# only for the Vite dev server, and without credentials: a cross-site page can never send the session cookie.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
    allow_credentials=False,
)



# The database being down (restart, crash) is reported as 503 with a plain message, not a bare 500.
@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(PoolTimeout)
async def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "The database is not reachable right now. "
                                                            "Try again in a moment."})


app.include_router(health.router)          # /health is public; /status needs a session (see health.py)
app.include_router(auth.router)
app.include_router(orgs.router)
app.include_router(projects.router)
app.include_router(sources.router)
app.include_router(graphs.router)
app.include_router(pipeline.router)
app.include_router(conversations.router)
app.include_router(usage.router)
app.include_router(models.router, dependencies=[Depends(current_user)])
app.include_router(tools.router, dependencies=[Depends(current_user)])

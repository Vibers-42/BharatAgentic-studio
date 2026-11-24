"""FastAPI entry point: connects to the database on startup and mounts the route files.
Run locally with: uvicorn app.main:app --reload"""

from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from .db import close_db, init_db
from .routes import graphs, health, models, pipeline, sources, tools, usage, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    close_db()


app = FastAPI(title="BharatAgent Studio API", lifespan=lifespan)

# The web container proxies /api, but allow the Vite dev server when running outside Docker.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)



# The database being down (restart, crash) is reported as 503 with a plain message, not a bare 500.
@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(PoolTimeout)
async def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "The database is not reachable right now. "
                                                            "Try again in a moment."})


app.include_router(health.router)
app.include_router(pipeline.router)
app.include_router(graphs.router)
app.include_router(sources.router)
app.include_router(models.router)
app.include_router(users.router)
app.include_router(usage.router)
app.include_router(tools.router)

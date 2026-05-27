"""FastAPI application: lifespan (schema + seed), CORS, and routers."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models  # noqa: F401  -- import so models register on Base.metadata
from .config import settings
from .db import Base, engine
from .routers import anomalies, fleet, telemetry, vehicles, zones
from .seed import seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema via create_all + idempotent seed (Alembic is the production path; see ADR).
    Base.metadata.create_all(bind=engine)
    seed(engine)
    yield


app = FastAPI(title="Fleet Telemetry Monitoring Service", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(telemetry.router)
app.include_router(zones.router)
app.include_router(vehicles.router)
app.include_router(anomalies.router)
app.include_router(fleet.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}

"""
CCMS API Gateway (FastAPI).

All service routers are mounted here under /api/<domain>. The gateway owns:
  - authentication parsing (cookies / Bearer)
  - CORS
  - logging
  - event-bus wiring at startup
  - index creation + admin seeding at startup

Each service module stays boundary-clean and only talks to other services via
the in-process event bus (never by direct imports of routers).
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# IMPORTANT: env must load before any module that reads env at import-time.
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from fastapi import APIRouter, FastAPI  # noqa: E402
from starlette.middleware.cors import CORSMiddleware  # noqa: E402

from core.db import close_client, create_indexes  # noqa: E402
from services.identity.router import router as identity_router  # noqa: E402
from services.identity.seed import seed as seed_identity  # noqa: E402
from services.patient.router import router as patient_router  # noqa: E402
from services.scheduling.router import router as scheduling_router  # noqa: E402
from services.communication.router import router as communication_router  # noqa: E402
from services.communication.subscribers import register as register_comm_subscribers  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ccms")

app = FastAPI(title="Chiropractic Clinic Management System")

# -------- API Gateway: mount all service routers under /api --------
api_router = APIRouter(prefix="/api")


@api_router.get("/")
async def root():
    return {"service": "CCMS API Gateway", "status": "ok"}


@api_router.get("/health")
async def health():
    return {"status": "healthy"}


api_router.include_router(identity_router)
api_router.include_router(patient_router)
api_router.include_router(scheduling_router)
api_router.include_router(communication_router)

app.include_router(api_router)

# -------- CORS --------
frontend_url = os.environ.get("FRONTEND_URL")
cors_origins_raw = os.environ.get("CORS_ORIGINS", "*")
if frontend_url:
    origins = [frontend_url, "http://localhost:3000"]
elif cors_origins_raw == "*":
    origins = ["*"]
else:
    origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]

# Browsers reject wildcard + credentials=true. When we have a wildcard, we
# disable credentials; otherwise we keep them on so cookies flow.
allow_credentials = origins != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------- Lifecycle --------
@app.on_event("startup")
async def on_startup():
    await create_indexes()
    register_comm_subscribers()
    await seed_identity()
    logger.info("CCMS startup complete (indexes, subscribers, seed).")


@app.on_event("shutdown")
async def on_shutdown():
    await close_client()

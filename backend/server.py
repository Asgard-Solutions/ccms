"""
CCMS API Gateway (FastAPI) — HIPAA-hardened build.
"""
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from fastapi import APIRouter, FastAPI, Request  # noqa: E402
from starlette.middleware.cors import CORSMiddleware  # noqa: E402

from core import metrics  # noqa: E402
from core.config import ensure_required as ensure_config  # noqa: E402
from core.db import close_client, create_indexes  # noqa: E402
from core.redis_client import close as close_redis, ping as redis_ping  # noqa: E402
from services.audit.router import router as audit_router  # noqa: E402
from services.communication.router import router as communication_router  # noqa: E402
from services.compliance.router import router as compliance_router  # noqa: E402
from services.communication.subscribers import register as register_comm_subscribers  # noqa: E402
from services.identity.router import router as identity_router  # noqa: E402
from services.identity.seed import seed as seed_identity  # noqa: E402
from services.patient.router import router as patient_router  # noqa: E402
from services.perf.router import router as perf_router, metrics_router  # noqa: E402
from services.privacy.router import router as privacy_router  # noqa: E402
from services.scheduling.router import router as scheduling_router  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ccms")

app = FastAPI(title="Chiropractic Clinic Management System")

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
api_router.include_router(audit_router)
api_router.include_router(compliance_router)
api_router.include_router(privacy_router)
api_router.include_router(perf_router)
api_router.include_router(metrics_router)  # GET /api/metrics

app.include_router(api_router)


# ---------- HTTP request timing middleware (Prometheus histogram) ----------
@app.middleware("http")
async def http_metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    # Bucket by path prefix so we don't explode label cardinality on dynamic IDs.
    path = request.url.path
    if path.startswith("/api/"):
        parts = path.split("/", 3)
        prefix = "/".join(parts[:3]) if len(parts) >= 3 else path  # /api/patients
    else:
        prefix = path
    status_class = f"{response.status_code // 100}xx"
    try:
        metrics.http_request_duration_seconds.labels(
            method=request.method, path_prefix=prefix, status_class=status_class
        ).observe(elapsed)
    except Exception:
        pass
    return response

frontend_url = os.environ.get("FRONTEND_URL")
cors_origins_raw = os.environ.get("CORS_ORIGINS", "*")
if frontend_url:
    origins = [frontend_url, "http://localhost:3000"]
elif cors_origins_raw == "*":
    origins = ["*"]
else:
    origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]
allow_credentials = origins != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Reauth-Required"],
)


@app.on_event("startup")
async def on_startup():
    ensure_config()  # fail-fast on missing MONGO_URL / DB_NAME / JWT_SECRET / DATA_ENCRYPTION_KEY
    await create_indexes()
    register_comm_subscribers()
    await seed_identity()
    redis_alive = await redis_ping()
    logger.info(
        "CCMS startup complete (HIPAA-hardened, redis_alive=%s).", redis_alive
    )


@app.on_event("shutdown")
async def on_shutdown():
    await close_redis()
    await close_client()

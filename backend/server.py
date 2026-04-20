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
from core.config import ensure_required as ensure_config, transport_warnings  # noqa: E402
from core.db import close_client, create_indexes  # noqa: E402
from core.error_handlers import install as install_error_handler  # noqa: E402
from core.logging_setup import configure as configure_logging  # noqa: E402
from core.security_headers import install as install_security_headers  # noqa: E402
from core.redis_client import close as close_redis, ping as redis_ping  # noqa: E402
from services.audit.router import router as audit_router  # noqa: E402
from services.clinic_profile.router import router as clinic_profile_router  # noqa: E402
from services.authz.router import router as authz_router  # noqa: E402
from services.authz.reporting import router as authz_reports_router  # noqa: E402
from services.authz.seed import seed_authz  # noqa: E402
from services.communication.router import router as communication_router  # noqa: E402
from services.compliance.router import router as compliance_router  # noqa: E402
from services.communication.subscribers import register as register_comm_subscribers  # noqa: E402
from services.compliance_ops.router import router as compliance_ops_router  # noqa: E402
from services.compliance_ops.seed import seed_compliance_ops  # noqa: E402
from services.exports import router as exports_router, cleanup_expired_exports  # noqa: E402
from services.identity.router import router as identity_router  # noqa: E402
from services.identity.seed import seed as seed_identity  # noqa: E402
from services.infra import router as infra_router  # noqa: E402
from services.patient.router import router as patient_router  # noqa: E402
from services.perf.router import router as perf_router, metrics_router  # noqa: E402
from services.privacy.router import router as privacy_router  # noqa: E402
from services.reports.router import router as reports_router  # noqa: E402
from services.scheduling.router import router as scheduling_router  # noqa: E402
from services.tenancy.router import router as tenancy_router  # noqa: E402
from services.tenancy.seed import seed_tenancy  # noqa: E402
from services.workforce.router import router as workforce_router  # noqa: E402


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
api_router.include_router(tenancy_router)
api_router.include_router(patient_router)
api_router.include_router(scheduling_router)
api_router.include_router(authz_router)
api_router.include_router(authz_reports_router)
api_router.include_router(communication_router)
api_router.include_router(compliance_router)
api_router.include_router(privacy_router)
api_router.include_router(audit_router)
api_router.include_router(perf_router)
api_router.include_router(reports_router)
api_router.include_router(exports_router)
api_router.include_router(compliance_ops_router)
api_router.include_router(infra_router)
api_router.include_router(workforce_router)
api_router.include_router(clinic_profile_router)
api_router.include_router(metrics_router)  # GET /api/metrics

app.include_router(api_router)

# Sanitised 500 responses + structured error logging.
install_error_handler(app)


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

# Layered security response headers. Must run AFTER CORSMiddleware is added
# so CORS preflight handlers still work, but before auth — because FastAPI
# middlewares execute in reverse registration order.
install_security_headers(app)


@app.on_event("startup")
async def on_startup():
    configure_logging()
    ensure_config()  # fail-fast on missing MONGO_URL / DB_NAME / JWT_SECRET / DATA_ENCRYPTION_KEY
    for warning in transport_warnings():
        logger.warning("Transport posture: %s", warning)
    await create_indexes()
    # Validate every required secret is present. Fail fast at startup.
    from core import secrets as _secrets
    missing = _secrets.validate_startup()
    if missing:
        logger.error("CCMS refusing to start — missing secrets: %s", missing)
        raise RuntimeError(f"Missing required secrets: {missing}")
    register_comm_subscribers()
    await seed_tenancy()      # must run BEFORE seed_identity so tenant rows exist
    await seed_identity()
    await seed_authz()
    await seed_compliance_ops()
    # Purge expired export artifacts (best-effort — errors are logged).
    try:
        await cleanup_expired_exports()
    except Exception as exc:  # noqa: BLE001
        logger.warning("export cleanup on boot failed: %s", exc)
    redis_alive = await redis_ping()
    logger.info(
        "CCMS startup complete (HIPAA-hardened, redis_alive=%s).", redis_alive
    )


@app.on_event("shutdown")
async def on_shutdown():
    await close_redis()
    await close_client()

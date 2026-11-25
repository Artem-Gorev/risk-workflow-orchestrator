import fcntl
import logging
from contextlib import asynccontextmanager, closing, contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.concurrency import contextmanager_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from backend.api.insights import router as insights
from backend.api.management import router as management
from backend.api.models import Failure
from backend.api.routes import router
from backend.api.verification import router as verification
from backend.config import BackendConfig
from backend.logging import record
from backend.migrations import upgrade_database
from backend.orchestrator.isolation import demo_schema, provision
from backend.orchestrator.storage import DomainError, query
from backend.runtime import Runtime


@contextmanager
def running_backend():
    config = BackendConfig.from_env()
    with open(config.lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        upgrade_database()
        with closing(Runtime(config)) as runtime:
            url = provision(runtime.storage, demo_schema(runtime.storage))
            demo_config = replace(
                config,
                database_url=url,
                worker_slots=2,
                worker_delay=0,
                daily_enabled=False,
                scheduler_poll=0.1,
                dispatcher_poll=0.1,
            )
            with closing(Runtime(demo_config)) as demo:
                runtime.demo = demo
                runtime.open()
                demo.open()
                yield runtime


@asynccontextmanager
async def lifespan(app):
    async with contextmanager_in_threadpool(running_backend()) as runtime:
        app.state.runtime = runtime
        yield


app = FastAPI(title="Risk Workflow Orchestrator", version="1.0", lifespan=lifespan)
for api_router in (router, management, insights, verification):
    app.include_router(api_router, responses={422: {"model": Failure}})


def error_response(status, code, message):
    return JSONResponse({"code": code, "message": message}, status_code=status)


@app.middleware("http")
async def identity(request: Request, call_next):
    config = request.app.state.runtime.config
    actor = config.demo_initiator
    if config.auth_mode == "proxy" and not request.url.path.startswith("/health/"):
        actor = request.headers.get("X-Authenticated-User")
        if not actor:
            return error_response(401, "AUTH_REQUIRED", "Требуется вход")
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("Origin")
        allowed = config.public_origin or str(request.base_url).rstrip("/")
        if origin and origin != allowed:
            return error_response(403, "ORIGIN_DENIED", "Источник запроса не разрешён")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return error_response(415, "JSON_REQUIRED", "Требуется application/json")
    request.state.actor = actor
    request.state.request_id = str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        record(
            "request",
            requestId=request.state.request_id,
            status=response.status_code,
            jobId=getattr(request.state, "job_id", request.path_params.get("job_id")),
            path=request.url.path,
        )
    return response


@app.exception_handler(DomainError)
async def domain_error(request, exc):
    return error_response(exc.status, exc.code, str(exc))


@app.exception_handler(SQLAlchemyError)
async def database_error(request, exc):
    logging.getLogger("uvicorn.error").exception("Ошибка БД requestId=%s", request.state.request_id)
    return error_response(503, "SERVICE_UNAVAILABLE", "Хранилище временно недоступно")


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    if any("calculationDate" in err["loc"] for err in exc.errors()):
        return error_response(422, "INVALID_CALCULATION_DATE", "Укажите календарную дату YYYY-MM-DD")
    return error_response(422, "VALIDATION_ERROR", "Проверьте параметры запроса")


@app.get("/health/live")
def live():
    return {"status": "live"}


@app.get("/health/ready")
@app.get("/api/v1/health/ready", include_in_schema=False)
def ready(request: Request):
    runtime = request.app.state.runtime
    with runtime.storage.engine.connect() as conn:
        query(conn, "SELECT 1")
    if any(not r.thread.is_alive() or not r.healthy for r in (runtime, runtime.demo)):
        return error_response(503, "NOT_READY", "Диспетчер временно недоступен")
    return {"status": "ready"}


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"
if FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS), name="assets")


@app.get("/", include_in_schema=False)
def dashboard():
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        return error_response(
            503, "FRONTEND_NOT_BUILT", "Соберите панель командой npm --prefix frontend run build"
        )
    return FileResponse(index, headers={"Cache-Control": "no-cache"})

from __future__ import annotations

import os
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware


from app.services.spatial_photo_service import RateLimitError, SpatialPhotoService, UploadValidationError

app = FastAPI(title="Spatial Photo Backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BASE_DIR = Path(__file__).resolve().parent.parent

ML_SHARP_COMMAND = os.getenv("ML_SHARP_COMMAND", f"python {BASE_DIR / 'scripts/run_ml_sharp.py'}")
output_root_raw = os.getenv("SPATIAL_OUTPUT_ROOT", str(BASE_DIR / "data/spatial-photos"))
OUTPUT_ROOT = Path(output_root_raw)

service = SpatialPhotoService(ml_sharp_command=ML_SHARP_COMMAND, output_root=OUTPUT_ROOT)
templates = Jinja2Templates(directory=str(BASE_DIR / "app/templates"))

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app/static")), name="static")
app.mount("/generated", StaticFiles(directory=str(service.output_root)), name="generated")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    message = "; ".join(err.get("msg", "Invalid request") for err in exc.errors()) or "Invalid request"
    return JSONResponse(status_code=422, content={"error": message, "job_id": None})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"error": str(exc) or "Unexpected server error", "job_id": None})


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/spatial-photos")
async def create_spatial_photo(
    request: Request,
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    user_id: str | None = Form(default=None),
) -> JSONResponse:
    client_ip = request.client.host if request.client and request.client.host else "unknown"
    try:
        service.rate_limiter.check_and_record(client_ip)
    except RateLimitError as exc:
        return JSONResponse(
            status_code=429,
            content={
                "error": "Too many uploads. Please wait before trying again.",
                "retry_after": exc.retry_after,
            },
            headers={"Retry-After": str(exc.retry_after)},
        )

    if not image.filename:
        return JSONResponse(status_code=422, content={"error": "Image filename is required"})

    try:
        content_length = request.headers.get("content-length")
        parsed_content_length = int(content_length) if content_length else None
    except ValueError:
        parsed_content_length = None

    try:
        image_bytes = await image.read()
        staged_path = service.validate_and_stage_upload(
            filename=image.filename,
            image_bytes=image_bytes,
            content_length=parsed_content_length,
        )
        job_id, input_path = service.create_pending_job_from_staged(staged_path, user_id=user_id)
    except UploadValidationError as exc:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.message})

    background_tasks.add_task(service.process_job, job_id, input_path, user_id)

    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "status": "pending",
            "status_url": f"/api/spatial-photos/{job_id}/status",
        },
    )


@app.get("/api/spatial-photos/{job_id}/status")
def spatial_photo_status(job_id: str) -> JSONResponse:
    status_payload = service.get_job_status(job_id)
    if status_payload is None:
        return JSONResponse(status_code=404, content={"error": "Unknown job_id", "job_id": job_id})

    return JSONResponse(status_code=200, content=status_payload)


@app.delete("/api/spatial-photos/{job_id}")
def delete_spatial_photo(job_id: str) -> JSONResponse:
    deleted, error, status_code = service.delete_job(job_id)
    if not deleted:
        return JSONResponse(status_code=status_code, content={"error": error, "job_id": job_id})
    return JSONResponse(status_code=200, content={"job_id": job_id, "deleted": True})


@app.get("/api/feed")
def feed(
    page: int = 1,
    page_size: int = 20,
    user_id: str | None = None,
    include_pending: str | None = None,
) -> JSONResponse:
    if page < 1:
        return JSONResponse(status_code=400, content={"error": "page must be >= 1"})
    if page_size < 1 or page_size > 100:
        return JSONResponse(status_code=400, content={"error": "page_size must be between 1 and 100"})

    include_pending_bool = str(include_pending).lower() in {"1", "true", "yes", "on"} if include_pending is not None else False

    return JSONResponse(
        status_code=200,
        content=service.get_feed(
            page=page,
            page_size=page_size,
            user_id=user_id,
            include_pending=include_pending_bool,
        ),
    )

import json
import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    File,
    Header,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse

from redis import Redis
from rq import Queue
from sqlalchemy import bindparam, text
from sqlmodel import Session, select

from models.ocr_job import OcrJob


router = APIRouter(
    prefix="/api/ocr",
    tags=["OCR"],
)


UPLOAD_DIRECTORY = Path(
    os.getenv(
        "UPLOAD_DIRECTORY",
        "/app/uploads",
    )
)


ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "application/pdf": ".pdf",
}


MAX_FILE_SIZE = 20 * 1024 * 1024


def get_queue() -> Queue:
    redis_url = os.getenv(
        "REDIS_URL",
        "redis://redis:6379/0",
    )

    queue_name = os.getenv(
        "OCR_QUEUE_NAME",
        "ocr",
    )

    redis_connection = Redis.from_url(
        redis_url,
    )

    return Queue(
        queue_name,
        connection=redis_connection,
    )


def build_safe_filename(
    original_filename: Optional[str],
    suffix: str,
) -> str:

    token = secrets.token_hex(16)

    return f"{token}{suffix}"


ADMIN_ROLES = ("admin", "superadmin")


def is_admin(user) -> bool:
    return user.role in ADMIN_ROLES


def can_access(user, job: OcrJob) -> bool:
    return is_admin(user) or job.owner_id == user.id


def owner_names(session: Session, owner_ids: set) -> dict:
    if not owner_ids:
        return {}

    rows = session.execute(
        text(
            "SELECT id, username FROM useraccount "
            "WHERE id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": list(owner_ids)},
    ).all()

    return {row[0]: row[1] for row in rows}


def job_to_dict(
    job: OcrJob,
    owner_username: Optional[str] = None,
) -> dict:
    structured_data = None

    if job.structured_data:
        try:
            structured_data = json.loads(
                job.structured_data
            )
        except json.JSONDecodeError:
            structured_data = None

    return {
        "id": job.id,
        "owner_id": job.owner_id,
        "owner_username": owner_username,
        "status": job.status,
        "document_type": job.document_type,
        "original_filename": job.original_filename,
        "mime_type": job.mime_type,
        "confidence": job.confidence,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "result": structured_data,
    }


def register_ocr_routes(
    app,
    engine,
    current_user,
):

    @router.post("/jobs")
    async def create_ocr_job(
        file: UploadFile = File(...),
        authorization: Optional[str] = Header(
            default=None
        ),
    ):

        user = current_user(
            authorization
        )

        if not user.id:
            raise HTTPException(
                status_code=401,
                detail="Utilisateur invalide.",
            )

        content_type = (
            file.content_type or ""
        ).lower()

        if content_type not in ALLOWED_CONTENT_TYPES:

            raise HTTPException(
                status_code=415,
                detail=(
                    "Format non pris en charge. "
                    "Utilisez JPEG, PNG, WebP, BMP ou PDF."
                ),
            )

        suffix = ALLOWED_CONTENT_TYPES[
            content_type
        ]

        filename = build_safe_filename(
            file.filename,
            suffix,
        )

        UPLOAD_DIRECTORY.mkdir(
            parents=True,
            exist_ok=True,
        )

        destination = (
            UPLOAD_DIRECTORY / filename
        )

        size = 0

        try:

            with destination.open("wb") as output:

                while True:

                    chunk = await file.read(
                        1024 * 1024
                    )

                    if not chunk:
                        break

                    size += len(chunk)

                    if size > MAX_FILE_SIZE:

                        raise HTTPException(
                            status_code=413,
                            detail=(
                                "Document trop volumineux. "
                                "Maximum : 20 Mo."
                            ),
                        )

                    output.write(chunk)

        except Exception:

            destination.unlink(
                missing_ok=True
            )

            raise

        finally:

            await file.close()

        with Session(engine) as session:

            ocr_job = OcrJob(
                owner_id=user.id,
                status="pending",
                original_filename=(
                    file.filename or filename
                ),
                mime_type=content_type,
                storage_path=str(
                    destination
                ),
            )

            session.add(ocr_job)
            session.commit()
            session.refresh(ocr_job)

            if not ocr_job.id:

                destination.unlink(
                    missing_ok=True
                )

                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Impossible de crÃ©er "
                        "le travail OCR."
                    ),
                )

            try:

                queue = get_queue()

                queue.enqueue(
                    "app.tasks.process_ocr_job",
                    ocr_job.id,
                    str(destination),
                    job_timeout=600,
                )

            except Exception as error:

                ocr_job.status = "failed"
                ocr_job.error_message = (
                    "Impossible d'envoyer "
                    "le document au worker OCR."
                )

                session.add(ocr_job)
                session.commit()

                destination.unlink(
                    missing_ok=True
                )

                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Le service OCR "
                        "est temporairement indisponible."
                    ),
                ) from error

            return job_to_dict(
                ocr_job
            )

    @router.get("/jobs")
    def list_ocr_jobs(
        authorization: Optional[str] = Header(
            default=None
        ),
    ):
        user = current_user(authorization)

        with Session(engine) as session:
            statement = select(OcrJob)

            if not is_admin(user):
                statement = statement.where(
                    OcrJob.owner_id == user.id
                )

            statement = (
                statement
                .order_by(OcrJob.created_at.desc())
                .limit(100)
            )

            jobs = session.exec(statement).all()
            names = owner_names(
                session,
                {job.owner_id for job in jobs},
            )

            return [
                job_to_dict(job, names.get(job.owner_id))
                for job in jobs
            ]

    @router.get("/jobs/{job_id}")
    def get_ocr_job(
        job_id: int,
        authorization: Optional[str] = Header(
            default=None
        ),
    ):
        user = current_user(authorization)

        with Session(engine) as session:
            job = session.get(OcrJob, job_id)

            if not job or not can_access(user, job):
                raise HTTPException(
                    status_code=404,
                    detail="Travail OCR introuvable.",
                )

            names = owner_names(session, {job.owner_id})

            return job_to_dict(job, names.get(job.owner_id))

    @router.get("/jobs/{job_id}/file")
    def get_ocr_job_file(
        job_id: int,
        authorization: Optional[str] = Header(
            default=None
        ),
    ):
        user = current_user(authorization)

        with Session(engine) as session:
            job = session.get(OcrJob, job_id)

            if not job or not can_access(user, job):
                raise HTTPException(
                    status_code=404,
                    detail="Travail OCR introuvable.",
                )

            storage_path = job.storage_path
            mime_type = job.mime_type

        path = Path(storage_path or "").resolve()

        if (
            not storage_path
            or UPLOAD_DIRECTORY.resolve() not in path.parents
            or not path.is_file()
        ):
            raise HTTPException(
                status_code=404,
                detail="Document original introuvable.",
            )

        return FileResponse(
            path,
            media_type=mime_type or "application/octet-stream",
            headers={
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    @router.delete("/jobs/{job_id}")
    def delete_ocr_job(
        job_id: int,
        authorization: Optional[str] = Header(
            default=None
        ),
    ):
        user = current_user(authorization)

        if not is_admin(user):
            raise HTTPException(
                status_code=403,
                detail="Droits administrateur requis.",
            )

        with Session(engine) as session:
            job = session.get(OcrJob, job_id)

            if not job:
                raise HTTPException(
                    status_code=404,
                    detail="Travail OCR introuvable.",
                )

            storage_path = job.storage_path

            session.delete(job)
            session.commit()

        if storage_path:
            path = Path(storage_path).resolve()

            if UPLOAD_DIRECTORY.resolve() in path.parents:
                path.unlink(missing_ok=True)

        return {"deleted": job_id}

    app.include_router(router)

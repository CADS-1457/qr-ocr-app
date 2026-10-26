import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from .engines.ppstructure import extract_structure
from .parsers.generic import normalize_document


def process_document(file_path: str) -> dict:
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(file_path)
    raw = extract_structure(path)
    return normalize_document(raw)


def _connect() -> psycopg.Connection:
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(url, autocommit=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def process_ocr_job(job_id: int, file_path: str) -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE ocrjob SET status = 'processing', started_at = %s WHERE id = %s",
            (_now(), job_id),
        )

        try:
            result = process_document(file_path)
        except Exception as error:
            connection.execute(
                "UPDATE ocrjob SET status = 'failed', error_message = %s, "
                "completed_at = %s WHERE id = %s",
                (f"{type(error).__name__}: {error}"[:1000], _now(), job_id),
            )
            raise

        connection.execute(
            "UPDATE ocrjob SET status = 'completed', document_type = %s, raw_text = %s, "
            "structured_data = %s, confidence = %s, error_message = NULL, "
            "completed_at = %s WHERE id = %s",
            (
                result.get("document_type"),
                result.get("raw_text"),
                json.dumps(result, ensure_ascii=False),
                result.get("confidence"),
                _now(),
                job_id,
            ),
        )

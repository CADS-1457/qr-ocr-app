from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class OcrJob(SQLModel, table=True):
    id: Optional[int] = Field(
        default=None,
        primary_key=True,
    )

    owner_id: int = Field(
        foreign_key="useraccount.id",
        index=True,
    )

    status: str = Field(
        default="pending",
        index=True,
        max_length=30,
    )

    document_type: Optional[str] = Field(
        default=None,
        index=True,
        max_length=50,
    )

    original_filename: Optional[str] = Field(
        default=None,
        max_length=255,
    )

    mime_type: Optional[str] = Field(
        default=None,
        max_length=100,
    )

    storage_path: Optional[str] = Field(
        default=None,
        max_length=1000,
    )

    raw_text: Optional[str] = None

    structured_data: Optional[str] = None

    confidence: Optional[float] = None

    error_message: Optional[str] = None

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )

    started_at: Optional[datetime] = None

    completed_at: Optional[datetime] = None
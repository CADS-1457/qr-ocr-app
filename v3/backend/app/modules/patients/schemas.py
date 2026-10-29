import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class PatientCreate(BaseModel):
    organization_id: uuid.UUID

    first_name: str = Field(
        min_length=1,
        max_length=150,
    )

    last_name: str = Field(
        min_length=1,
        max_length=150,
    )

    birth_date: date | None = None

    administrative_sex: str | None = Field(
        default=None,
        max_length=30,
    )

    preferred_language: str = Field(
        default="fr-BE",
        min_length=2,
        max_length=10,
    )

    active: bool = True


class PatientUpdate(BaseModel):
    first_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=150,
    )

    last_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=150,
    )

    birth_date: date | None = None

    administrative_sex: str | None = Field(
        default=None,
        max_length=30,
    )

    preferred_language: str | None = Field(
        default=None,
        min_length=2,
        max_length=10,
    )

    active: bool | None = None


class PatientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID

    first_name: str
    last_name: str

    birth_date: date | None
    administrative_sex: str | None

    preferred_language: str
    active: bool

    created_at: datetime
    created_by: uuid.UUID | None = None

    updated_at: datetime
    updated_by: uuid.UUID | None = None

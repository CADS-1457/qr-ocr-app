import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.patients.exceptions import (
    PatientNotFoundError,
)
from app.modules.patients.schemas import (
    PatientCreate,
    PatientRead,
    PatientUpdate,
)
from app.modules.patients.service import (
    PatientsService,
)


router = APIRouter(
    prefix="/patients",
    tags=["patients"],
)


DbSession = Annotated[
    Session,
    Depends(get_db),
]


@router.get("/health")
def health():
    return {
        "module": "patients",
        "status": "ok",
    }


@router.post(
    "",
    response_model=PatientRead,
    status_code=status.HTTP_201_CREATED,
)
def create_patient(
    payload: PatientCreate,
    db: DbSession,
):
    service = PatientsService(db)

    try:
        return service.create_patient(
            payload
        )

    except IntegrityError as exc:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Le patient ne peut pas être créé "
                "en raison d'une contrainte de données."
            ),
        ) from exc


@router.get(
    "",
    response_model=list[PatientRead],
)
def list_patients(
    organization_id: uuid.UUID,
    db: DbSession,
    skip: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
    active_only: bool = True,
):
    service = PatientsService(db)

    return service.list_patients(
        organization_id=organization_id,
        skip=skip,
        limit=limit,
        active_only=active_only,
    )


@router.get(
    "/{patient_id}",
    response_model=PatientRead,
)
def get_patient(
    patient_id: uuid.UUID,
    organization_id: uuid.UUID,
    db: DbSession,
):
    service = PatientsService(db)

    try:
        return service.get_patient(
            patient_id=patient_id,
            organization_id=organization_id,
        )

    except PatientNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.patch(
    "/{patient_id}",
    response_model=PatientRead,
)
def update_patient(
    patient_id: uuid.UUID,
    organization_id: uuid.UUID,
    payload: PatientUpdate,
    db: DbSession,
):
    service = PatientsService(db)

    try:
        return service.update_patient(
            patient_id=patient_id,
            organization_id=organization_id,
            payload=payload,
        )

    except PatientNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except IntegrityError as exc:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "La modification ne peut pas être "
                "enregistrée en raison d'une "
                "contrainte de données."
            ),
        ) from exc
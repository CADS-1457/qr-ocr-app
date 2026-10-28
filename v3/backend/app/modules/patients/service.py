import uuid

from sqlalchemy.orm import Session

from app.modules.patients.exceptions import (
    PatientNotFoundError,
)
from app.modules.patients.models import Patient
from app.modules.patients.repository import (
    PatientsRepository,
)
from app.modules.patients.schemas import (
    PatientCreate,
    PatientUpdate,
)


class PatientsService:
    """Point d'entrée métier du module patients."""

    def __init__(self, db: Session):
        self.repository = PatientsRepository(db)

    def create_patient(
        self,
        payload: PatientCreate,
    ) -> Patient:
        return self.repository.create(payload)

    def list_patients(
        self,
        organization_id: uuid.UUID,
        skip: int = 0,
        limit: int = 100,
        active_only: bool = True,
    ) -> list[Patient]:
        return self.repository.list(
            organization_id=organization_id,
            skip=skip,
            limit=limit,
            active_only=active_only,
        )

    def get_patient(
        self,
        patient_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> Patient:
        patient = self.repository.get(
            patient_id=patient_id,
            organization_id=organization_id,
        )

        if patient is None:
            raise PatientNotFoundError(
                patient_id
            )

        return patient

    def update_patient(
        self,
        patient_id: uuid.UUID,
        organization_id: uuid.UUID,
        payload: PatientUpdate,
    ) -> Patient:
        patient = self.get_patient(
            patient_id=patient_id,
            organization_id=organization_id,
        )

        return self.repository.update(
            patient=patient,
            payload=payload,
        )
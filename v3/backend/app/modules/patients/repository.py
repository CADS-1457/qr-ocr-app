import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.patients.models import Patient
from app.modules.patients.schemas import (
    PatientCreate,
    PatientUpdate,
)


class PatientsRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        payload: PatientCreate,
    ) -> Patient:
        patient = Patient(
            **payload.model_dump()
        )

        self.db.add(patient)
        self.db.commit()
        self.db.refresh(patient)

        return patient

    def list(
        self,
        organization_id: uuid.UUID,
        skip: int = 0,
        limit: int = 100,
        active_only: bool = True,
    ) -> list[Patient]:
        statement = (
            select(Patient)
            .where(
                Patient.organization_id
                == organization_id
            )
            .order_by(
                Patient.last_name,
                Patient.first_name,
            )
            .offset(skip)
            .limit(limit)
        )

        if active_only:
            statement = statement.where(
                Patient.active.is_(True)
            )

        return list(
            self.db.scalars(statement).all()
        )

    def get(
        self,
        patient_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> Patient | None:
        statement = select(Patient).where(
            Patient.id == patient_id,
            Patient.organization_id
            == organization_id,
        )

        return self.db.scalar(statement)

    def update(
        self,
        patient: Patient,
        payload: PatientUpdate,
    ) -> Patient:
        changes = payload.model_dump(
            exclude_unset=True
        )

        for field_name, value in changes.items():
            setattr(
                patient,
                field_name,
                value,
            )

        self.db.add(patient)
        self.db.commit()
        self.db.refresh(patient)

        return patient
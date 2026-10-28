import uuid


class PatientNotFoundError(Exception):
    """Patient introuvable dans l'organisation demandée."""

    def __init__(self, patient_id: uuid.UUID):
        self.patient_id = patient_id

        super().__init__(
            f"Patient {patient_id} introuvable."
        )
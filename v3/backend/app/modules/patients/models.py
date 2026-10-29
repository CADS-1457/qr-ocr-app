import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Patient(Base):
    __tablename__ = "patient"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "national_number_hmac",
            name="patient_organization_id_national_number_hmac_key",
        ),
        {"schema": "patient"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Les contraintes de clés étrangères vers core.organization et
    # core.app_user existent dans PostgreSQL.
    #
    # Elles ne sont volontairement pas déclarées ici avec ForeignKey
    # tant que les modèles SQLAlchemy du module core ne sont pas chargés
    # dans les mêmes métadonnées.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    national_number_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
    )

    national_number_hmac: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
    )

    first_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    last_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    birth_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    administrative_sex: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    preferred_language: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="fr-BE",
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

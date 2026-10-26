import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Consumption(BaseModel):
    stock_unit_id: UUID
    quantity: Decimal = Field(gt=0)
    comment: str | None = Field(default=None, max_length=2000)


class FollowUp(BaseModel):
    required: bool = False
    appointment_type_id: UUID | None = None
    doctor_id: UUID | None = None
    target_date: date | None = None
    notes: str | None = Field(default=None, max_length=4000)


class TransferRequest(BaseModel):
    to_location_id: UUID
    start_now: bool = False
    new_state: Literal[
        "PLANNED",
        "URGENT",
        "PRESENT",
        "WAITING",
        "IN_PROGRESS",
        "RECOVERY",
        "BILLING",
        "COMPLETED",
        "CANCELLED",
        "NO_SHOW",
        "POSTPONED",
    ]
    handover_comment: str | None = Field(default=None, max_length=4000)
    recovery_comment: str | None = Field(default=None, max_length=4000)
    discharge_instructions: str | None = Field(default=None, max_length=8000)
    discharge_notes: str | None = Field(default=None, max_length=4000)
    follow_up: FollowUp = Field(default_factory=FollowUp)
    consumptions: list[Consumption] = Field(default_factory=list)


class StatusRequest(BaseModel):
    new_state: str = Field(min_length=1, max_length=60)
    comment: str | None = Field(default=None, max_length=4000)


class PatientCreate(BaseModel):
    nom: str = Field(min_length=1, max_length=255)
    prenom: str = Field(min_length=1, max_length=255)
    date_naissance: date | None = None
    mobile: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    language: str | None = Field(default=None, max_length=10)
    temporary: bool = False
    force: bool = False
    prenoms: str | None = Field(default=None, max_length=255)
    lieu_naissance: str | None = Field(default=None, max_length=255)
    sexe: str | None = Field(default=None, max_length=1)
    nationalite: str | None = Field(default=None, max_length=100)
    adresse: str | None = Field(default=None, max_length=255)
    code_postal: str | None = Field(default=None, max_length=20)
    commune: str | None = Field(default=None, max_length=255)
    numero_national: str | None = Field(default=None, max_length=20)
    photo: str | None = Field(default=None, max_length=400000)


class PatientReconcile(BaseModel):
    target_id: int | None = None
    prenoms: str | None = Field(default=None, max_length=255)
    lieu_naissance: str | None = Field(default=None, max_length=255)
    sexe: str | None = Field(default=None, max_length=1)
    nationalite: str | None = Field(default=None, max_length=100)
    adresse: str | None = Field(default=None, max_length=255)
    code_postal: str | None = Field(default=None, max_length=20)
    commune: str | None = Field(default=None, max_length=255)
    numero_national: str | None = Field(default=None, max_length=20)
    photo: str | None = Field(default=None, max_length=400000)
    nom: str | None = Field(default=None, max_length=255)
    prenom: str | None = Field(default=None, max_length=255)
    date_naissance: date | None = None
    mobile: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    language: str | None = Field(default=None, max_length=10)


class RestoreDeletedAppointment(BaseModel):
    starts_at: datetime | None = None
    room_id: UUID | None = None
    doctor_id: UUID | None = None
    appointment_type_id: UUID | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=1440)


class AppointmentCreate(BaseModel):
    patient_id: int = Field(gt=0)
    appointment_type_id: UUID | None = None
    room_id: UUID
    doctor_id: UUID | None = None
    operator_id: UUID | None = None
    starts_at: datetime
    ends_at: datetime
    phone: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=4000)
    # Choix propre au rendez-vous : prime sur le réglage par défaut de la salle (None = suivre la salle).
    show_doctor: bool | None = None
    show_status: bool | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "AppointmentCreate":
        if self.ends_at <= self.starts_at:
            raise ValueError("La fin doit être postérieure au début.")
        return self


class StatusCreate(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    color: str = Field(default="#64748B", pattern=r"^#[0-9A-Fa-f]{6}$")
    edge: bool = False


class StatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, min_length=1, max_length=60)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    edge: bool | None = None
    active: bool | None = None


class RoomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["WAITING", "CONSULTATION", "OPERATING", "RECOVERY", "BILLING", "OTHER"] = "OTHER"
    color: str = Field(default="#64748B", pattern=r"^#[0-9A-Fa-f]{6}$")
    capacity: int = Field(default=1, gt=0)
    max_parallel: int = Field(default=1, ge=1, le=20)


class AgendaGroupPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    room_ids: list[UUID] = Field(min_length=1, max_length=50)


class RoomUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    kind: Literal["WAITING", "CONSULTATION", "OPERATING", "RECOVERY", "BILLING", "OTHER"] | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    capacity: int | None = Field(default=None, gt=0)
    max_parallel: int | None = Field(default=None, ge=1, le=20)
    show_doctor: bool | None = None
    show_status: bool | None = None
    default_state: Literal[
        "PRESENT", "WAITING", "IN_PROGRESS", "RECOVERY", "BILLING", "COMPLETED", ""
    ] | None = None

class SchedulingBlockBase(BaseModel):
    resource_id: UUID | None = None
    start_time: time
    end_time: time
    slot_minutes: int = Field(ge=1, le=1440)
    label: str | None = Field(default=None, max_length=100)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    valid_from: date | None = None
    valid_to: date | None = None

    @model_validator(mode="after")
    def validate_block(self) -> "SchedulingBlockBase":
        self.start_time = self.start_time.replace(second=0, microsecond=0, tzinfo=None)
        self.end_time = self.end_time.replace(second=0, microsecond=0, tzinfo=None)
        if self.end_time <= self.start_time:
            raise ValueError("La fin doit être postérieure au début.")
        span = (self.end_time.hour - self.start_time.hour) * 60 + (
            self.end_time.minute - self.start_time.minute
        )
        if self.slot_minutes > span:
            raise ValueError("Le pas dépasse la durée de la plage.")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("La date de fin de validité précède la date de début.")
        return self


class SchedulingBlockCreate(SchedulingBlockBase):
    room_id: UUID
    weekdays: list[int] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def validate_weekdays(self) -> "SchedulingBlockCreate":
        if any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("Jour de semaine invalide (0 = lundi à 6 = dimanche).")
        self.weekdays = sorted(set(self.weekdays))
        return self


class SchedulingBlockUpdate(SchedulingBlockBase):
    weekday: int = Field(ge=0, le=6)
    active: bool = True


class AppointmentTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    duration_minutes: int = Field(ge=0, le=1440)
    color: str = Field(default="#2563EB", pattern=r"^#[0-9A-Fa-f]{6}$")


class ResourceCreate(BaseModel):
    kind: Literal["DOCTOR", "OPERATOR"]
    display_name: str = Field(min_length=1, max_length=255)


class RoomOrder(BaseModel):
    room_ids: list[UUID] = Field(min_length=1, max_length=200)


class AppointmentTypeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    duration_minutes: int | None = Field(default=None, ge=0, le=1440)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class ResourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["DOCTOR", "OPERATOR"] | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=255)


class StockLocationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: UUID | None = None
    room_ids: list[UUID] = Field(default_factory=list)


class StockLocationRooms(BaseModel):
    room_ids: list[UUID] = Field(default_factory=list)


class StockItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    sku: str | None = Field(default=None, max_length=255)
    track_lot: bool = False
    track_serial: bool = False
    consumable: bool = True


class StockUnitCreate(BaseModel):
    stock_location_id: UUID
    stock_item_id: UUID
    lot_number: str | None = Field(default=None, max_length=255)
    serial_number: str | None = Field(default=None, max_length=255)
    expires_on: date | None = None
    quantity: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def validate_tracked_fields(self) -> "StockUnitCreate":
        if self.serial_number is not None and self.quantity != 1:
            raise ValueError("Une unité avec numéro de série doit avoir une quantité de 1.")
        return self


class ExceptionTypePayload(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: str = Field(default="#64748b", pattern=r"^#[0-9a-fA-F]{6}$")
    blocks_booking: bool = True


class ExceptionPayload(BaseModel):
    type_id: UUID
    room_id: UUID | None = None
    resource_id: UUID | None = None
    start_date: date
    end_date: date
    start_time: time | None = None
    end_time: time | None = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check_dates(self) -> "ExceptionPayload":
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValueError("L'heure de fin doit être après l'heure de début.")
        if self.end_date < self.start_date:
            raise ValueError("La date de fin précède la date de début.")
        if (self.end_date - self.start_date).days > 366:
            raise ValueError("Une exception ne peut pas dépasser 367 jours.")
        return self

class PlannedStockAdd(BaseModel):
    code: str | None = Field(default=None, max_length=255)
    stock_unit_id: UUID | None = None
    quantity: Decimal = Field(default=Decimal("1"), gt=0, le=1000)

    @model_validator(mode="after")
    def check_target(self) -> "PlannedStockAdd":
        if not (self.code and self.code.strip()) and self.stock_unit_id is None:
            raise ValueError("Indiquez le code scanné ou l'unité de stock.")
        return self
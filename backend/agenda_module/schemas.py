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
    new_state: Literal[
        "PLANNED",
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
    comment: str | None = Field(default=None, max_length=4000)


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

    @model_validator(mode="after")
    def validate_range(self) -> "AppointmentCreate":
        if self.ends_at <= self.starts_at:
            raise ValueError("La fin doit être postérieure au début.")
        return self


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
    duration_minutes: int = Field(gt=0, le=1440)
    color: str = Field(default="#2563EB", pattern=r"^#[0-9A-Fa-f]{6}$")


class ResourceCreate(BaseModel):
    kind: Literal["DOCTOR", "OPERATOR"]
    display_name: str = Field(min_length=1, max_length=255)


class RoomOrder(BaseModel):
    room_ids: list[UUID] = Field(min_length=1, max_length=200)


class AppointmentTypeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    duration_minutes: int | None = Field(default=None, gt=0, le=1440)
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

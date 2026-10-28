import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import Column, LargeBinary, delete, func, inspect, or_, text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, create_engine, select

from agenda_module.router import router as agenda_router
from agenda_module.portal import admin as portal_admin_router, ensure_portal_schema, portal as portal_public_router
from models.ocr_job import OcrJob  # noqa: F401 (enregistre la table dans SQLModel)
from routes.ocr import register_ocr_routes
MAX_IMAGE_SIZE = 10 * 1024 * 1024
ADMIN_TOKEN_TTL_SECONDS = 8 * 60 * 60
AUDIT_RETENTION_DAYS = 365
IMAGE_SIGNATURES = {
    "image/png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
    "image/gif": lambda data: data.startswith((b"GIF87a", b"GIF89a")),
    "image/webp": lambda data: data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    "image/bmp": lambda data: data.startswith(b"BM"),
}


class ScanBase(SQLModel):
    qr_code: Optional[str] = Field(default=None, index=True, max_length=2000)
    ocr_text: Optional[str] = None
    nom: Optional[str] = Field(default=None, index=True, max_length=255)
    reference: Optional[str] = Field(default=None, index=True, max_length=255)
    numero_serie: Optional[str] = Field(default=None, index=True, max_length=255)
    numero_lot: Optional[str] = Field(default=None, index=True, max_length=255)
    commentaire: Optional[str] = None


class Scan(ScanBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: Optional[int] = Field(default=None)
    image_data: Optional[bytes] = Field(
        default=None,
        sa_column=Column(LargeBinary, nullable=True),
    )
    image_content_type: Optional[str] = Field(default=None, max_length=100)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )


class ScanCreate(ScanBase):
    image_base64: Optional[str] = None
    image_content_type: Optional[str] = Field(default=None, max_length=100)


class ScanUpdate(SQLModel):
    qr_code: Optional[str] = Field(default=None, max_length=2000)
    ocr_text: Optional[str] = None
    nom: Optional[str] = Field(default=None, max_length=255)
    reference: Optional[str] = Field(default=None, max_length=255)
    numero_serie: Optional[str] = Field(default=None, max_length=255)
    numero_lot: Optional[str] = Field(default=None, max_length=255)
    commentaire: Optional[str] = None


class ScanRead(ScanBase):
    id: int
    owner_id: Optional[int] = None
    created_at: datetime
    image_content_type: Optional[str] = None


class AdminLogin(SQLModel):
    username: str
    password: str


class UserAccount(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, max_length=255, unique=True)
    password_hash: str
    role: str = Field(default="user", max_length=20)
    active: bool = Field(default=True)
    agenda_enabled: bool = Field(default=False)
    doctor_id: Optional[int] = Field(default=None)
    auth_version: int = Field(default=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AppSecret(SQLModel, table=True):
    name: str = Field(primary_key=True, max_length=100)
    value: str


class UserCreate(SQLModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=12, max_length=1024)
    role: Literal["admin", "user"] = "user"
    agenda_enabled: bool = False
    doctor_id: Optional[int] = None


class UserUpdate(SQLModel):
    username: Optional[str] = Field(default=None, min_length=1, max_length=255)
    password: Optional[str] = Field(default=None, min_length=12, max_length=1024)
    role: Optional[Literal["admin", "user"]] = None
    active: Optional[bool] = None
    agenda_enabled: Optional[bool] = None
    doctor_id: Optional[int] = None


class UserRead(SQLModel):
    id: int
    username: str
    role: str
    active: bool
    agenda_enabled: bool = False
    doctor_id: Optional[int] = None
    created_at: datetime


class ScanChange(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    scan_id: int = Field(index=True)
    action: str = Field(max_length=20)
    changed_by: str = Field(max_length=255)
    changed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )
    before_data: str
    after_data: Optional[str] = None


class ScanChangeRead(SQLModel):
    id: int
    scan_id: int
    action: str
    changed_by: str
    changed_at: datetime
    before_data: str
    after_data: Optional[str] = None


class AuditEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    entity_type: str = Field(max_length=50, index=True)
    entity_id: int = Field(index=True)
    action: str = Field(max_length=30, index=True)
    changed_by: str = Field(max_length=255, index=True)
    changed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )
    before_data: Optional[str] = None
    after_data: Optional[str] = None


class AuditEventRead(SQLModel):
    id: int
    entity_type: str
    entity_id: int
    action: str
    changed_by: str
    changed_at: datetime
    before_data: Optional[str] = None
    after_data: Optional[str] = None


class AuditEventPage(SQLModel):
    items: list[AuditEventRead]
    total: int
    offset: int
    limit: int


class StockItemBase(SQLModel):
    denomination: str = Field(min_length=1, max_length=255)
    quantity: int = Field(ge=0)
    min_quantity: int = Field(default=0, ge=0)
    entry_date: date
    code_barres: Optional[str] = Field(default=None, max_length=255)
    numero_lot: Optional[str] = Field(default=None, max_length=255)
    numero_serie: Optional[str] = Field(default=None, max_length=255)
    expiration_date: Optional[date] = None


class StockItem(StockItemBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)


class StockQrUnit(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    stock_item_id: int = Field(foreign_key="stockitem.id", index=True)
    numero_serie: str = Field(max_length=255, unique=True, index=True)
    quantity: int = Field(default=1, ge=0)
    entry_date: date
    numero_lot: Optional[str] = Field(default=None, max_length=255)
    expiration_date: Optional[date] = None
    commentaire: Optional[str] = Field(default=None, max_length=2000)
    created_by: str = Field(max_length=255)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )


class StockQrUnitRead(SQLModel):
    id: int
    stock_item_id: int
    denomination: str
    numero_serie: str
    quantity: int
    entry_date: date
    numero_lot: Optional[str]
    expiration_date: Optional[date]
    commentaire: Optional[str] = None
    created_by: str
    created_at: datetime


class StockQrUnitUpdate(SQLModel):
    numero_serie: Optional[str] = Field(default=None, min_length=1, max_length=255)
    entry_date: Optional[date] = None
    numero_lot: Optional[str] = Field(default=None, max_length=255)
    expiration_date: Optional[date] = None
    commentaire: Optional[str] = Field(default=None, max_length=2000)


class StockItemCreate(StockItemBase):
    pass


class StockItemUpdate(SQLModel):
    denomination: Optional[str] = Field(default=None, min_length=1, max_length=255)
    quantity: Optional[int] = Field(default=None, ge=0)
    min_quantity: Optional[int] = Field(default=None, ge=0)
    entry_date: Optional[date] = None
    code_barres: Optional[str] = Field(default=None, max_length=255)
    numero_lot: Optional[str] = Field(default=None, max_length=255)
    numero_serie: Optional[str] = Field(default=None, max_length=255)
    expiration_date: Optional[date] = None


class StockItemRead(StockItemBase):
    id: int


class StockWithdrawalRequest(SQLModel):
    quantity: int = Field(gt=0)


class StockWithdrawal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    stock_item_id: int = Field(index=True)
    patient_id: Optional[int] = Field(default=None, index=True)
    denomination: str = Field(max_length=255)
    quantity: int = Field(gt=0)
    withdrawn_by: str = Field(max_length=255)
    withdrawn_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )


class StockWithdrawalRead(SQLModel):
    id: int
    stock_item_id: int
    patient_id: Optional[int] = None
    patient_name: Optional[str] = None
    denomination: str
    quantity: int
    withdrawn_by: str
    withdrawn_at: datetime


class StockQrAddition(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    stock_item_id: int = Field(index=True)
    denomination: str = Field(max_length=255)
    numero_serie: str = Field(max_length=255)
    quantity_after: int = Field(ge=1)
    added_by: str = Field(max_length=255)
    added_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )


class StockQrAdditionRequest(SQLModel):
    stock_item_id: int = Field(gt=0)
    numero_serie: str = Field(min_length=1, max_length=255)
    expiration_date: Optional[date] = None


class StockQrAdditionRead(SQLModel):
    id: int
    stock_item_id: int
    denomination: str
    numero_serie: str
    quantity_added: int
    quantity_after: Optional[int] = None
    added_by: str
    added_at: datetime


class Patient(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nom: str = Field(max_length=255, index=True)
    prenom: str = Field(max_length=255, index=True)
    date_naissance: date
    mobile: Optional[str] = Field(default=None, max_length=50)
    email: Optional[str] = Field(default=None, max_length=255)
    language: Optional[str] = Field(default=None, max_length=10)
    sms_consent: bool = Field(default=False)
    email_consent: bool = Field(default=False)
    is_temporary: bool = Field(default=False)
    prenoms: Optional[str] = Field(default=None, max_length=255)
    lieu_naissance: Optional[str] = Field(default=None, max_length=255)
    sexe: Optional[str] = Field(default=None, max_length=1)
    nationalite: Optional[str] = Field(default=None, max_length=100)
    adresse: Optional[str] = Field(default=None, max_length=255)
    code_postal: Optional[str] = Field(default=None, max_length=20)
    commune: Optional[str] = Field(default=None, max_length=255)
    numero_national: Optional[str] = Field(default=None, max_length=11)
    created_by: str = Field(max_length=255)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )


class PatientRead(SQLModel):
    id: int
    nom: str
    prenom: str
    date_naissance: date
    mobile: Optional[str] = None
    email: Optional[str] = None
    language: Optional[str] = None
    sms_consent: bool = False
    email_consent: bool = False
    is_temporary: bool = False
    prenoms: Optional[str] = None
    lieu_naissance: Optional[str] = None
    sexe: Optional[str] = None
    nationalite: Optional[str] = None
    adresse: Optional[str] = None
    code_postal: Optional[str] = None
    commune: Optional[str] = None
    numero_national: Optional[str] = None
    created_at: datetime


def normalize_national_number(value: Optional[str]) -> Optional[str]:
    """Numéro national belge : 11 chiffres, clé de contrôle modulo 97 (naissances avant et après 2000)."""
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return None
    if len(digits) != 11:
        raise HTTPException(status_code=422, detail="Le numéro national doit comporter 11 chiffres.")
    base, check = int(digits[:9]), int(digits[9:])
    if not any(97 - (base + extra) % 97 == check for extra in (0, 2_000_000_000)):
        raise HTTPException(status_code=422, detail="Numéro national invalide (clé de contrôle).")
    return digits


MAX_PHOTO_BYTES = 200_000


def decode_photo(value: Optional[str]) -> Optional[bytes]:
    """Photo JPEG de la carte (base64) ; vérifie la signature et la taille."""
    if not value:
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(status_code=422, detail="Photo invalide.")
    if len(raw) > MAX_PHOTO_BYTES or not raw.startswith(b"\xff\xd8"):
        raise HTTPException(status_code=422, detail="La photo doit être une image JPEG de moins de 200 Ko.")
    return raw


PATIENT_LANGUAGES = {"fr", "nl", "de", "en"}
PATIENT_MOBILE_PATTERN = re.compile(r"^\+?[0-9][0-9 .\-/()]{5,29}$")
PATIENT_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PatientUpdate(SQLModel):
    nom: Optional[str] = Field(default=None, min_length=1, max_length=255)
    prenom: Optional[str] = Field(default=None, min_length=1, max_length=255)
    date_naissance: Optional[date] = None
    mobile: Optional[str] = Field(default=None, max_length=50)
    email: Optional[str] = Field(default=None, max_length=255)
    language: Optional[str] = Field(default=None, max_length=10)
    sms_consent: Optional[bool] = None
    email_consent: Optional[bool] = None
    prenoms: Optional[str] = Field(default=None, max_length=255)
    lieu_naissance: Optional[str] = Field(default=None, max_length=255)
    sexe: Optional[str] = Field(default=None, max_length=1)
    nationalite: Optional[str] = Field(default=None, max_length=100)
    adresse: Optional[str] = Field(default=None, max_length=255)
    code_postal: Optional[str] = Field(default=None, max_length=20)
    commune: Optional[str] = Field(default=None, max_length=255)
    numero_national: Optional[str] = Field(default=None, max_length=20)


class PatientPhotoPayload(SQLModel):
    photo: str = Field(min_length=1, max_length=400_000)


class StockScanResolveRequest(SQLModel):
    code: str = Field(min_length=1, max_length=2000)


class StockScanMatch(SQLModel):
    stock_item_id: int
    denomination: str
    quantity: int
    scanned_code: str
    qr_unit_id: Optional[int] = None
    numero_serie: Optional[str] = None
    expiration_date: Optional[date] = None
    numero_lot: Optional[str] = None


class StockDispenseRequest(SQLModel):
    quantity: int = Field(gt=0)
    medecin: Optional[str] = Field(default=None, max_length=255)
    salle: Optional[str] = Field(default=None, max_length=255)
    patient_id: Optional[int] = Field(default=None, gt=0)
    nom: Optional[str] = Field(default=None, max_length=255)
    prenom: Optional[str] = Field(default=None, max_length=255)
    date_naissance: Optional[date] = None
    scanned_code: str = Field(min_length=1, max_length=2000)
    qr_unit_id: Optional[int] = Field(default=None, gt=0)


class PatientDispensation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    patient_id: int = Field(foreign_key="patient.id", index=True)
    stock_item_id: int = Field(foreign_key="stockitem.id", index=True)
    withdrawal_id: int = Field(foreign_key="stockwithdrawal.id", unique=True)
    denomination: str = Field(max_length=255)
    quantity: int = Field(gt=0)
    scanned_code: str = Field(max_length=2000)
    numero_serie: Optional[str] = Field(default=None, max_length=255)
    expiration_date: Optional[date] = None
    medecin: Optional[str] = Field(default=None, max_length=255)
    salle: Optional[str] = Field(default=None, max_length=255)
    dispensed_by: str = Field(max_length=255)
    dispensed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )


class PatientDispensationRead(SQLModel):
    id: int
    patient_id: int
    stock_item_id: int
    withdrawal_id: int
    patient_name: str
    patient_date_naissance: date
    denomination: str
    quantity: int
    scanned_code: str
    numero_serie: Optional[str]
    expiration_date: Optional[date] = None
    medecin: Optional[str] = None
    salle: Optional[str] = None
    dispensed_by: str
    dispensed_at: datetime
    stock_remaining: int


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./scans.db",
)

engine_options = {
    "pool_pre_ping": True,
}

if DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {
        "check_same_thread": False,
    }

engine = create_engine(
    DATABASE_URL,
    **engine_options,
)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    with engine.begin() as price_connection:
        price_connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS stock_prices("
            "id SERIAL PRIMARY KEY, stock_item_id INTEGER NOT NULL REFERENCES stockitem(id) ON DELETE CASCADE, "
            "price NUMERIC(12,2) NOT NULL CHECK(price>=0), valid_from DATE NOT NULL, valid_to DATE, "
            "created_by VARCHAR(255), created_at TIMESTAMP NOT NULL DEFAULT now(), "
            "CHECK(valid_to IS NULL OR valid_to>=valid_from))"
        )
        price_connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_stock_prices_item ON stock_prices(stock_item_id, valid_from)"
        )
    with engine.begin() as doctor_connection:
        doctor_connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS doctors("
            "id SERIAL PRIMARY KEY, inami6 VARCHAR(6) NOT NULL UNIQUE, inami11 VARCHAR(11) NOT NULL UNIQUE, "
            "nom VARCHAR(120) NOT NULL, prenom VARCHAR(120) NOT NULL, specialite VARCHAR(120), "
            "email VARCHAR(255), tel VARCHAR(40), gsm VARCHAR(40), adresse VARCHAR(255), "
            "code_postal VARCHAR(20), ville VARCHAR(120), active BOOLEAN NOT NULL DEFAULT TRUE, "
            "resource_id UUID UNIQUE REFERENCES people_resources(id) ON DELETE SET NULL, "
            "stats_access BOOLEAN NOT NULL DEFAULT FALSE, amounts_access BOOLEAN NOT NULL DEFAULT FALSE, "
            "billing_access BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMP NOT NULL DEFAULT now())"
        )
        doctor_connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS doctor_groups("
            "id SERIAL PRIMARY KEY, name VARCHAR(120) NOT NULL UNIQUE, description VARCHAR(255), "
            "stats_access BOOLEAN NOT NULL DEFAULT FALSE, amounts_access BOOLEAN NOT NULL DEFAULT FALSE, "
            "billing_access BOOLEAN NOT NULL DEFAULT FALSE, group_scope BOOLEAN NOT NULL DEFAULT FALSE, "
            "created_at TIMESTAMP NOT NULL DEFAULT now())"
        )
        doctor_connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS doctor_group_members("
            "group_id INTEGER NOT NULL REFERENCES doctor_groups(id) ON DELETE CASCADE, "
            "doctor_id INTEGER NOT NULL REFERENCES doctors(id) ON DELETE CASCADE, "
            "PRIMARY KEY(group_id, doctor_id))"
        )
        doctor_connection.exec_driver_sql(
            "ALTER TABLE useraccount ADD COLUMN IF NOT EXISTS doctor_id INTEGER REFERENCES doctors(id) ON DELETE SET NULL"
        )
        doctor_connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_useraccount_doctor ON useraccount(doctor_id) WHERE doctor_id IS NOT NULL"
        )
    ensure_portal_schema(engine)
    scan_columns = {column["name"] for column in inspect(engine).get_columns("scan")}
    user_columns = {column["name"] for column in inspect(engine).get_columns("useraccount")}
    stock_item_columns = {
        column["name"] for column in inspect(engine).get_columns("stockitem")
    }
    stock_withdrawal_columns = {
        column["name"] for column in inspect(engine).get_columns("stockwithdrawal")
    }
    patient_dispensation_columns = {
        column["name"] for column in inspect(engine).get_columns("patientdispensation")
    }
    patient_columns = {column["name"] for column in inspect(engine).get_columns("patient")}
    with engine.begin() as connection:
        for column_name, column_type in (
            ("mobile", "VARCHAR(50)"),
            ("email", "VARCHAR(255)"),
            ("language", "VARCHAR(10)"),
            ("sms_consent", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("email_consent", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("is_temporary", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("prenoms", "VARCHAR(255)"),
            ("lieu_naissance", "VARCHAR(255)"),
            ("sexe", "VARCHAR(1)"),
            ("nationalite", "VARCHAR(100)"),
            ("adresse", "VARCHAR(255)"),
            ("code_postal", "VARCHAR(20)"),
            ("commune", "VARCHAR(255)"),
            ("numero_national", "VARCHAR(11)"),
            ("photo", "BYTEA"),
        ):
            if column_name not in patient_columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE patient ADD COLUMN {column_name} {column_type}"
                )
        if engine.dialect.name == "postgresql" and inspect(engine).has_table("appointments"):
            connection.exec_driver_sql(
                "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS show_doctor BOOLEAN"
            )
            connection.exec_driver_sql(
                "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS show_status BOOLEAN"
            )
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS appointment_status_colors("
                "state text PRIMARY KEY, color text NOT NULL)"
            )
        if engine.dialect.name == "postgresql" and inspect(engine).has_table("appointments"):
            connection.exec_driver_sql("ALTER TYPE appointment_state ADD VALUE IF NOT EXISTS 'URGENT'")
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS appointment_statuses("
                "code text PRIMARY KEY, label text NOT NULL, color text NOT NULL, "
                "edge boolean NOT NULL DEFAULT false, builtin boolean NOT NULL DEFAULT false, "
                "active boolean NOT NULL DEFAULT true, sort_order integer NOT NULL DEFAULT 0)"
            )
            connection.exec_driver_sql(
                "INSERT INTO appointment_statuses(code,label,color,edge,builtin,sort_order) VALUES "
                "('PLANNED','Planifié','#94A3B8',false,true,1),('URGENT','Urgence','#DC2626',true,true,2),('PRESENT','Présent','#16A34A',false,true,3),('WAITING','En attente','#F59E0B',false,true,4),('IN_PROGRESS','En cours','#2563EB',false,true,5),('RECOVERY','Réveil','#7C3AED',false,true,6),('BILLING','Facturation','#0891B2',false,true,7),('COMPLETED','Terminé','#475569',false,true,8),('CANCELLED','Annulé','#DC2626',false,true,9),('NO_SHOW','Absent','#B45309',false,true,10),('POSTPONED','Reporté','#9CA3AF',false,true,11),('DISCHARGED','Sorti','#475569',false,true,12) "
                "ON CONFLICT (code) DO NOTHING"
            )
        if engine.dialect.name == "postgresql" and inspect(engine).has_table("rooms"):
            connection.exec_driver_sql(
                "ALTER TABLE rooms ADD COLUMN IF NOT EXISTS show_status boolean NOT NULL DEFAULT false"
            )
        if engine.dialect.name == "postgresql" and inspect(engine).has_table("appointment_types"):
            connection.exec_driver_sql(
                "ALTER TABLE appointment_types "
                "DROP CONSTRAINT IF EXISTS appointment_types_duration_minutes_check"
            )
            connection.exec_driver_sql(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint "
                "WHERE conname='appointment_types_duration_nonneg') THEN "
                "ALTER TABLE appointment_types ADD CONSTRAINT appointment_types_duration_nonneg "
                "CHECK(duration_minutes>=0); END IF; END $$"
            )
        if "image_data" not in scan_columns:
            connection.exec_driver_sql("ALTER TABLE scan ADD COLUMN image_data BLOB")
        if "image_content_type" not in scan_columns:
            connection.exec_driver_sql(
                "ALTER TABLE scan ADD COLUMN image_content_type VARCHAR(100)"
            )
        if "numero_lot" not in scan_columns:
            connection.exec_driver_sql("ALTER TABLE scan ADD COLUMN numero_lot VARCHAR(255)")
        if "owner_id" not in scan_columns:
            connection.exec_driver_sql("ALTER TABLE scan ADD COLUMN owner_id INTEGER")
        if "auth_version" not in user_columns:
            connection.exec_driver_sql(
                "ALTER TABLE useraccount ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0"
            )
        if "agenda_enabled" not in user_columns:
            connection.exec_driver_sql(
                "ALTER TABLE useraccount ADD COLUMN agenda_enabled BOOLEAN NOT NULL DEFAULT FALSE"
            )
            # Les comptes existants gardent l'accès dont ils disposaient jusque-là.
            connection.exec_driver_sql("UPDATE useraccount SET agenda_enabled=TRUE")
        if "code_barres" not in stock_item_columns:
            connection.exec_driver_sql(
                "ALTER TABLE stockitem ADD COLUMN code_barres VARCHAR(255)"
            )
        if "min_quantity" not in stock_item_columns:
            connection.exec_driver_sql(
                "ALTER TABLE stockitem ADD COLUMN min_quantity INTEGER NOT NULL DEFAULT 0"
            )
        if "patient_id" not in stock_withdrawal_columns:
            connection.exec_driver_sql(
                "ALTER TABLE stockwithdrawal ADD COLUMN patient_id INTEGER"
            )
        if "medecin" not in patient_dispensation_columns:
            connection.exec_driver_sql(
                "ALTER TABLE patientdispensation ADD COLUMN medecin VARCHAR(255)"
            )
        for column, ddl in (
            ("validated_at", "TIMESTAMP"),
            ("validated_by", "VARCHAR(255)"),
            ("validated_price", "NUMERIC(12,2)"),
            ("billed_at", "TIMESTAMP"),
            ("billed_by", "VARCHAR(255)"),
        ):
            if column not in patient_dispensation_columns:
                connection.exec_driver_sql(f"ALTER TABLE patientdispensation ADD COLUMN {column} {ddl}")
        if "salle" not in patient_dispensation_columns:
            connection.exec_driver_sql(
                "ALTER TABLE patientdispensation ADD COLUMN salle VARCHAR(255)"
            )
        if "expiration_date" not in patient_dispensation_columns:
            connection.exec_driver_sql(
                "ALTER TABLE patientdispensation ADD COLUMN expiration_date DATE"
            )
        qr_unit_columns = {
            column["name"] for column in inspect(connection).get_columns("stockqrunit")
        }
        if "commentaire" not in qr_unit_columns:
            connection.exec_driver_sql(
                "ALTER TABLE stockqrunit ADD COLUMN commentaire VARCHAR(2000)"
            )
    seed_initial_admin()
    seed_initial_superadmin()
    backfill_scan_owners()
    backfill_audit_from_scan_changes()
    purge_expired_changes()


def seed_initial_admin() -> None:
    with Session(engine) as session:
        if session.exec(select(UserAccount.id).limit(1)).first() is not None:
            return
        username, password = admin_configuration()
        session.add(
            UserAccount(
                username=username,
                password_hash=hash_password(password),
                role="admin",
                active=True,
            )
        )
        session.commit()


def seed_initial_superadmin() -> None:
    configured = superadmin_configuration()
    if configured is None:
        return
    username, password = configured
    with Session(engine) as session:
        existing_superadmin = session.exec(
            select(UserAccount.id).where(UserAccount.role == "superadmin").limit(1)
        ).first()
        if existing_superadmin is not None:
            return
        existing_username = session.exec(
            select(UserAccount).where(UserAccount.username == username)
        ).first()
        if existing_username is not None:
            raise RuntimeError(
                "SUPERADMIN_USERNAME est déjà utilisé par un compte qui n'est pas SuperAdmin."
            )
        session.add(
            UserAccount(
                username=username,
                password_hash=hash_password(password),
                role="superadmin",
                active=True,
            )
        )
        session.commit()


def backfill_scan_owners() -> None:
    with Session(engine) as session:
        statement = (
            select(Scan, UserAccount.id)
            .join(ScanChange, ScanChange.scan_id == Scan.id)
            .join(UserAccount, UserAccount.username == ScanChange.changed_by)
            .where(
                Scan.owner_id.is_(None),
                ScanChange.action == "création",
                ScanChange.changed_at >= UserAccount.created_at,
            )
        )
        for scan, owner_id in session.exec(statement).all():
            scan.owner_id = owner_id
            session.add(scan)
        session.commit()


def backfill_audit_from_scan_changes() -> None:
    marker_name = "audit_scan_changes_backfilled_v1"
    with Session(engine) as session:
        if session.get(AppSecret, marker_name) is not None:
            return
        changes = session.exec(select(ScanChange)).all()
        for change in changes:
            session.add(
                AuditEvent(
                    entity_type="scan",
                    entity_id=change.scan_id,
                    action=change.action,
                    changed_by=change.changed_by,
                    changed_at=change.changed_at,
                    before_data=(
                        None if change.action == "création" else change.before_data
                    ),
                    after_data=change.after_data,
                )
            )
        session.add(AppSecret(name=marker_name, value="1"))
        session.commit()


def purge_expired_changes():
    expiration_date = datetime.now(timezone.utc).timestamp() - AUDIT_RETENTION_DAYS * 24 * 60 * 60
    with Session(engine) as session:
        expired_before = datetime.fromtimestamp(expiration_date, timezone.utc)
        session.exec(
            delete(ScanChange).where(ScanChange.changed_at < expired_before)
        )
        session.commit()


def admin_configuration() -> tuple[str, str]:
    username = os.getenv("ADMIN_USERNAME", "")
    password = os.getenv("ADMIN_PASSWORD", "")
    if (
        not username
        or len(username.encode("utf-8")) > 255
        or not password
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "Aucun compte n'existe encore. Définissez ADMIN_USERNAME et "
                "ADMIN_PASSWORD pour créer le compte administrateur initial."
            ),
        )
    return username, password


def superadmin_configuration() -> Optional[tuple[str, str]]:
    username = os.getenv("SUPERADMIN_USERNAME", "")
    password = os.getenv("SUPERADMIN_PASSWORD", "")
    if not username and not password:
        return None
    if (
        not username
        or len(username.encode("utf-8")) > 255
        or len(password) < 12
    ):
        raise RuntimeError(
            "Définissez SUPERADMIN_USERNAME et SUPERADMIN_PASSWORD "
            "(12 caractères minimum) pour créer le compte SuperAdmin."
        )
    return username, password


def admin_secret_key() -> str:
    configured_secret = os.getenv("ADMIN_SECRET_KEY")
    if configured_secret:
        if len(configured_secret) < 32:
            raise HTTPException(
                status_code=503,
                detail="ADMIN_SECRET_KEY doit contenir au moins 32 caractères.",
            )
        return configured_secret

    with Session(engine) as session:
        stored_secret = session.get(AppSecret, "session_signing_key")
        if stored_secret is not None:
            if len(stored_secret.value) < 32:
                raise HTTPException(
                    status_code=503,
                    detail="La clé de session stockée est invalide. Configurez ADMIN_SECRET_KEY.",
                )
            return stored_secret.value

        generated_secret = secrets.token_urlsafe(48)
        session.add(
            AppSecret(name="session_signing_key", value=generated_secret)
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            stored_secret = session.get(AppSecret, "session_signing_key")
            if stored_secret is None:
                raise RuntimeError(
                    "La clé de session n'a pas pu être persistée dans la base."
                )
            if len(stored_secret.value) < 32:
                raise HTTPException(
                    status_code=503,
                    detail="La clé de session stockée est invalide. Configurez ADMIN_SECRET_KEY.",
                )
            return stored_secret.value
        return generated_secret


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 310_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iteration_text, salt_hex, digest_hex = encoded_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iteration_text)
        if not 100_000 <= iterations <= 1_000_000:
            return False
        salt = bytes.fromhex(salt_hex)
        expected_digest = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False
    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual_digest, expected_digest)


def create_admin_token(user: UserAccount, secret_key: str) -> str:
    payload = json.dumps(
        {
            "uid": user.id,
            "ver": user.auth_version,
            "exp": int(datetime.now(timezone.utc).timestamp()) + ADMIN_TOKEN_TTL_SECONDS,
            "nonce": secrets.token_urlsafe(12),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    encoded_payload = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(
        secret_key.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded_payload}.{signature}"


def current_user(authorization: Optional[str] = Header(default=None)) -> UserAccount:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Connexion requise.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        encoded_payload, supplied_signature = authorization[7:].split(".", 1)
        encoded_payload_bytes = encoded_payload.encode("ascii")
        supplied_signature_bytes = supplied_signature.encode("ascii")
        payload = json.loads(
            base64.urlsafe_b64decode(
                encoded_payload + "=" * (-len(encoded_payload) % 4)
            )
        )
        user_id = int(payload["uid"])
        auth_version = int(payload["ver"])
        expires_at = int(payload["exp"])
    except (
        ValueError,
        KeyError,
        TypeError,
        UnicodeEncodeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as error:
        raise HTTPException(status_code=401, detail="Jeton administrateur invalide.") from error

    secret_key = admin_secret_key()
    expected_signature = hmac.new(
        secret_key.encode("utf-8"),
        encoded_payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    if (
        user_id <= 0
        or not hmac.compare_digest(
            supplied_signature_bytes, expected_signature.encode("ascii")
        )
        or expires_at <= int(datetime.now(timezone.utc).timestamp())
    ):
        raise HTTPException(status_code=401, detail="Session expirée ou jeton invalide.")
    with Session(engine) as session:
        user = session.get(UserAccount, user_id)
        if not user or not user.active or user.auth_version != auth_version:
            raise HTTPException(status_code=401, detail="Compte utilisateur inactif ou introuvable.")
        return user


def require_admin(authorization: Optional[str] = Header(default=None)) -> UserAccount:
    user = current_user(authorization)
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Droits administrateur requis.")
    return user


def require_superadmin(
    authorization: Optional[str] = Header(default=None),
) -> UserAccount:
    user = current_user(authorization)
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Droits SuperAdmin requis.")
    return user


def has_agenda_access(user: UserAccount) -> bool:
    return user.role == "superadmin" or bool(user.agenda_enabled)


def audit_snapshot(entity: SQLModel) -> str:
    excluded_fields = {"password_hash", "auth_version", "image_data"}
    values = {
        column.name: getattr(entity, column.name)
        for column in inspect(type(entity)).columns
        if column.name not in excluded_fields
    }
    return json.dumps(
        values,
        ensure_ascii=False,
        default=lambda value: value.isoformat() if isinstance(value, (date, datetime)) else str(value),
    )


def record_audit_event(
    session: Session,
    entity_type: str,
    entity_id: int,
    action: str,
    changed_by: str,
    before_data: Optional[str],
    after_data: Optional[str],
) -> None:
    session.add(
        AuditEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            changed_by=changed_by,
            before_data=before_data,
            after_data=after_data,
        )
    )


def scan_snapshot(scan: Scan) -> str:
    fields = (
        "qr_code",
        "ocr_text",
        "nom",
        "reference",
        "numero_serie",
        "numero_lot",
        "commentaire",
    )
    return json.dumps({field: getattr(scan, field) for field in fields}, ensure_ascii=False)


def record_scan_change(
    session: Session,
    scan: Scan,
    changed_by: str,
    action: str,
    before_data: str,
    after_data: Optional[str],
) -> None:
    session.add(
        ScanChange(
            scan_id=scan.id,
            action=action,
            changed_by=changed_by,
            before_data=before_data,
            after_data=after_data,
        )
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(title="API QR Code et OCR", version="1.0.0", lifespan=lifespan)

app.include_router(agenda_router)
app.include_router(portal_public_router)
app.include_router(portal_admin_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

register_ocr_routes(app, engine, current_user)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/auth/login")
def admin_login(payload: AdminLogin):
    secret_key = admin_secret_key()
    with Session(engine) as session:
        user = session.exec(
            select(UserAccount).where(UserAccount.username == payload.username)
        ).first()
        password_matches = bool(
            user
            and verify_password(payload.password, user.password_hash)
        )
        if not user or not user.active or not password_matches:
            raise HTTPException(status_code=401, detail="Identifiants incorrects ou compte inactif.")
        user_data = UserRead.model_validate(user)
        access_token = create_admin_token(user, secret_key)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ADMIN_TOKEN_TTL_SECONDS,
        "user": user_data,
    }


@app.get("/api/admin/users", response_model=list[UserRead])
def list_users(authorization: Optional[str] = Header(default=None)):
    require_admin(authorization)
    with Session(engine) as session:
        return list(
            session.exec(
                select(UserAccount)
                .where(UserAccount.role != "superadmin")
                .order_by(UserAccount.username)
            ).all()
        )


@app.post("/api/admin/users", response_model=UserRead, status_code=201)
def create_user(payload: UserCreate, authorization: Optional[str] = Header(default=None)):
    user_actor = require_admin(authorization)
    if payload.agenda_enabled and user_actor.role != "superadmin":
        raise HTTPException(
            status_code=403,
            detail="Seul le SuperAdmin peut activer le module Agenda.",
        )
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=422, detail="Le nom d'utilisateur est requis.")
    with Session(engine) as session:
        existing = session.exec(
            select(UserAccount.id).where(UserAccount.username == username)
        ).first()
        if existing is not None:
            raise HTTPException(status_code=409, detail="Ce nom d'utilisateur existe déjà.")
        _check_doctor_link(session, payload.doctor_id, None)
        user = UserAccount(
            username=username,
            password_hash=hash_password(payload.password),
            role=payload.role,
            active=True,
            agenda_enabled=payload.agenda_enabled,
            doctor_id=payload.doctor_id,
        )
        session.add(user)
        session.flush()
        if user.id is None:
            raise RuntimeError("L'identifiant du compte utilisateur n'a pas été généré.")
        record_audit_event(
            session,
            "compte utilisateur",
            user.id,
            "création",
            user_actor.username,
            None,
            audit_snapshot(user),
        )
        session.commit()
        session.refresh(user)
        return user


@app.patch("/api/admin/users/{user_id}", response_model=UserRead)
def update_user(
    user_id: int,
    payload: UserUpdate,
    authorization: Optional[str] = Header(default=None),
):
    user_actor = require_admin(authorization)
    changes = payload.model_dump(exclude_unset=True)
    if "agenda_enabled" in changes:
        if user_actor.role != "superadmin":
            raise HTTPException(
                status_code=403,
                detail="Seul le SuperAdmin peut activer ou retirer le module Agenda.",
            )
        if changes["agenda_enabled"] is None:
            raise HTTPException(status_code=422, detail="L'accès Agenda est requis.")
    if not changes:
        raise HTTPException(status_code=422, detail="Aucune donnée à modifier.")
    if "username" in changes:
        if changes["username"] is None:
            raise HTTPException(status_code=422, detail="Le nom d'utilisateur ne peut pas être vide.")
        changes["username"] = changes["username"].strip()
        if not changes["username"]:
            raise HTTPException(status_code=422, detail="Le nom d'utilisateur ne peut pas être vide.")
    if "password" in changes:
        if changes["password"] is None:
            raise HTTPException(status_code=422, detail="Le mot de passe ne peut pas être vide.")
        changes["password_hash"] = hash_password(changes.pop("password"))
    if "role" in changes and changes["role"] is None:
        raise HTTPException(status_code=422, detail="Le rôle ne peut pas être vide.")
    if "active" in changes and changes["active"] is None:
        raise HTTPException(status_code=422, detail="L'état du compte est requis.")

    with Session(engine) as session:
        user = session.get(UserAccount, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        if user.role == "superadmin":
            raise HTTPException(
                status_code=404,
                detail="Utilisateur introuvable.",
            )
        before_data = audit_snapshot(user)
        if "doctor_id" in changes:
            _check_doctor_link(session, changes["doctor_id"], user_id)
        if "username" in changes:
            existing = session.exec(
                select(UserAccount.id).where(
                    UserAccount.username == changes["username"],
                    UserAccount.id != user_id,
                )
            ).first()
            if existing is not None:
                raise HTTPException(status_code=409, detail="Ce nom d'utilisateur existe déjà.")
        next_role = changes.get("role", user.role)
        next_active = changes.get("active", user.active)
        account_changed = "password_hash" in changes or any(
            field in changes and changes[field] != getattr(user, field)
            for field in ("username", "role", "active")
        )
        if account_changed:
            changes["auth_version"] = user.auth_version + 1
        if user.role == "admin" and user.active and (
            next_role != "admin" or not next_active
        ):
            active_admins = session.exec(
                select(UserAccount.id).where(
                    UserAccount.role == "admin",
                    UserAccount.active.is_(True),
                )
            ).all()
            if len(active_admins) <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="Impossible de désactiver ou rétrograder le dernier administrateur actif.",
                )
        for field, value in changes.items():
            setattr(user, field, value)
        session.add(user)
        after_data = audit_snapshot(user)
        if "password_hash" in changes:
            after_values = json.loads(after_data)
            after_values["password_changed"] = True
            after_data = json.dumps(after_values, ensure_ascii=False)
        record_audit_event(
            session,
            "compte utilisateur",
            user.id,
            "modification",
            user_actor.username,
            before_data,
            after_data,
        )
        session.commit()
        session.refresh(user)
        return user


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, authorization: Optional[str] = Header(default=None)):
    admin = require_admin(authorization)
    with Session(engine) as session:
        user = session.get(UserAccount, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        if user.role == "superadmin":
            raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
        if user.id == admin.id:
            raise HTTPException(status_code=409, detail="Vous ne pouvez pas supprimer votre propre compte.")
        if user.role == "admin" and user.active:
            active_admins = session.exec(
                select(UserAccount.id).where(
                    UserAccount.role == "admin",
                    UserAccount.active.is_(True),
                )
            ).all()
            if len(active_admins) <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="Impossible de supprimer le dernier administrateur actif.",
                )
        record_audit_event(
            session,
            "compte utilisateur",
            user.id,
            "suppression",
            admin.username,
            audit_snapshot(user),
            None,
        )
        session.delete(user)
        session.commit()
    return {"success": True}


@app.get("/api/auth/me", response_model=UserRead)
def get_current_user(authorization: Optional[str] = Header(default=None)):
    return current_user(authorization)


@app.post("/api/admin/login", include_in_schema=False)
def legacy_admin_login(payload: AdminLogin):
    return admin_login(payload)


@app.post("/api/scans", response_model=ScanRead, status_code=201)
def create_scan(payload: ScanCreate, authorization: Optional[str] = Header(default=None)):
    user = current_user(authorization)
    image_data = None
    if payload.image_base64:
        if payload.image_content_type not in IMAGE_SIGNATURES:
            raise HTTPException(status_code=422, detail="Format d'image non pris en charge.")
        if len(payload.image_base64) > ((MAX_IMAGE_SIZE + 2) // 3) * 4:
            raise HTTPException(status_code=413, detail="L'image dépasse la limite de 10 Mo.")
        try:
            image_data = base64.b64decode(payload.image_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise HTTPException(status_code=422, detail="Image encodée invalide.") from error
        if not image_data:
            raise HTTPException(status_code=422, detail="L'image est vide.")
        if len(image_data) > MAX_IMAGE_SIZE:
            raise HTTPException(status_code=413, detail="L'image dépasse la limite de 10 Mo.")
        if not IMAGE_SIGNATURES[payload.image_content_type](image_data):
            raise HTTPException(status_code=422, detail="Le contenu ne correspond pas au format annoncé.")
    elif payload.image_content_type:
        raise HTTPException(status_code=422, detail="Les données de l'image sont manquantes.")

    if not payload.qr_code and not payload.ocr_text and not image_data:
        raise HTTPException(
            status_code=422,
            detail="Un QR Code, un texte OCR ou une image est requis.",
        )

    scan = Scan.model_validate(
        payload.model_dump(exclude={"image_base64", "image_content_type"})
    )
    scan.image_data = image_data
    scan.image_content_type = payload.image_content_type if image_data else None
    scan.owner_id = user.id
    purge_expired_changes()
    with Session(engine) as session:
        session.add(scan)
        session.flush()
        if scan.id is None:
            raise RuntimeError("L'identifiant du scan n'a pas été généré.")
        record_scan_change(
            session,
            scan,
            user.username,
            "création",
            "{}",
            scan_snapshot(scan),
        )
        record_audit_event(
            session,
            "scan",
            scan.id,
            "création",
            user.username,
            None,
            audit_snapshot(scan),
        )
        session.commit()
        session.refresh(scan)
        return scan


@app.get("/api/scans", response_model=list[ScanRead])
def list_scans(authorization: Optional[str] = Header(default=None)):
    current_user(authorization)
    with Session(engine) as session:
        statement = select(Scan).order_by(Scan.created_at.desc()).limit(100)
        return list(session.exec(statement).all())


@app.get("/api/scans/{scan_id}/image")
def get_scan_image(
    scan_id: int,
    authorization: Optional[str] = Header(default=None),
):
    current_user(authorization)
    with Session(engine) as session:
        scan = session.get(Scan, scan_id)
        if not scan:
            raise HTTPException(status_code=404, detail="Enregistrement introuvable.")
        if not scan.image_data or not scan.image_content_type:
            raise HTTPException(status_code=404, detail="Aucune image pour cet enregistrement.")
        return Response(
            content=scan.image_data,
            media_type=scan.image_content_type,
            headers={"X-Content-Type-Options": "nosniff"},
        )


@app.get("/api/admin/modifications", response_model=list[ScanChangeRead])
def list_scan_changes(
    scan_id: Optional[int] = None,
    authorization: Optional[str] = Header(default=None),
):
    actor = require_admin(authorization)
    purge_expired_changes()
    with Session(engine) as session:
        statement = select(ScanChange).order_by(ScanChange.changed_at.desc())
        if scan_id is not None:
            statement = statement.where(ScanChange.scan_id == scan_id)
        if actor.role != "superadmin":
            superadmin_names = session.exec(
                select(UserAccount.username).where(UserAccount.role == "superadmin")
            ).all()
            if superadmin_names:
                statement = statement.where(
                    ScanChange.changed_by.not_in(superadmin_names)
                )
        return list(session.exec(statement).all())


@app.get("/api/superadmin/audit", response_model=AuditEventPage)
def list_audit_events(
    offset: int = 0,
    limit: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    require_superadmin(authorization)
    if offset < 0 or limit < 1 or limit > 500:
        raise HTTPException(
            status_code=422,
            detail="La pagination doit utiliser offset >= 0 et limit entre 1 et 500.",
        )
    with Session(engine) as session:
        total = session.exec(select(func.count()).select_from(AuditEvent)).one()
        items = session.exec(
            select(AuditEvent)
            .order_by(AuditEvent.changed_at.desc(), AuditEvent.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return {
            "items": items,
            "total": total,
            "offset": offset,
            "limit": limit,
        }


@app.patch("/api/scans/{scan_id}", response_model=ScanRead)
def update_scan(
    scan_id: int,
    payload: ScanUpdate,
    authorization: Optional[str] = Header(default=None),
):
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Aucune donnée à modifier.")

    user = current_user(authorization)
    purge_expired_changes()
    with Session(engine) as session:
        scan = session.get(Scan, scan_id)
        if not scan:
            raise HTTPException(status_code=404, detail="Enregistrement introuvable.")
        if user.role not in ("admin", "superadmin") and scan.owner_id != user.id:
            raise HTTPException(
                status_code=403,
                detail="Vous ne pouvez modifier que les enregistrements que vous avez créés.",
            )

        updated_values = {
            field: changes.get(field, getattr(scan, field))
            for field in ("qr_code", "ocr_text")
        }
        if not any(updated_values.values()) and not scan.image_data:
            raise HTTPException(
                status_code=422,
                detail="Un QR Code, un texte OCR ou une image est requis.",
            )

        before_data = scan_snapshot(scan)
        audit_before_data = audit_snapshot(scan)
        for field, value in changes.items():
            setattr(scan, field, value)
        session.add(scan)
        after_data = scan_snapshot(scan)
        if before_data != after_data:
            record_scan_change(
                session,
                scan,
                user.username,
                "modification",
                before_data,
                after_data,
            )
            record_audit_event(
                session,
                "scan",
                scan.id,
                "modification",
                user.username,
                audit_before_data,
                audit_snapshot(scan),
            )
        session.commit()
        session.refresh(scan)
        return scan


@app.delete("/api/scans/{scan_id}")
def delete_scan(scan_id: int, authorization: Optional[str] = Header(default=None)):
    admin = require_admin(authorization)
    purge_expired_changes()
    with Session(engine) as session:
        scan = session.get(Scan, scan_id)
        if not scan:
            raise HTTPException(status_code=404, detail="Enregistrement introuvable.")
        record_scan_change(
            session,
            scan,
            admin.username,
            "suppression",
            scan_snapshot(scan),
            None,
        )
        record_audit_event(
            session,
            "scan",
            scan.id,
            "suppression",
            admin.username,
            audit_snapshot(scan),
            None,
        )
        session.delete(scan)
        session.commit()
    return {"success": True}


class StockPriceCreate(SQLModel):
    price: float = Field(ge=0, le=1000000)
    valid_from: date
    valid_to: Optional[date] = None


def _price_row(row) -> dict:
    return {
        "id": row["id"], "stock_item_id": row["stock_item_id"], "price": float(row["price"]),
        "valid_from": row["valid_from"], "valid_to": row["valid_to"],
    }


@app.get("/api/stock/prices/current")
def current_stock_prices(authorization: Optional[str] = Header(default=None)):
    current_user(authorization)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT DISTINCT ON (stock_item_id) stock_item_id, price FROM stock_prices "
                "WHERE valid_from <= CURRENT_DATE AND (valid_to IS NULL OR valid_to >= CURRENT_DATE) "
                "ORDER BY stock_item_id, valid_from DESC"
            )
        ).all()
    return {str(item_id): float(price) for item_id, price in rows}


@app.get("/api/stock/{item_id}/prices")
def list_stock_prices(item_id: int, authorization: Optional[str] = Header(default=None)):
    current_user(authorization)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, stock_item_id, price, valid_from, valid_to FROM stock_prices "
                "WHERE stock_item_id=:i ORDER BY valid_from DESC"
            ),
            {"i": item_id},
        ).mappings().all()
    return [_price_row(row) for row in rows]


@app.post("/api/stock/{item_id}/prices", status_code=201)
def add_stock_price(
    item_id: int,
    payload: StockPriceCreate,
    authorization: Optional[str] = Header(default=None),
):
    user = require_admin(authorization)
    if payload.valid_to is not None and payload.valid_to < payload.valid_from:
        raise HTTPException(status_code=422, detail="La date de fin précède la date d’application.")
    with engine.begin() as connection:
        if not connection.execute(text("SELECT 1 FROM stockitem WHERE id=:i"), {"i": item_id}).first():
            raise HTTPException(status_code=404, detail="Article introuvable.")
        new_id = _apply_price(connection, item_id, payload, user.username)
    return {"id": new_id}


def _apply_price(connection, item_id: int, payload: StockPriceCreate, username: str) -> int:
    start, end = payload.valid_from, payload.valid_to
    if True:
        existing = connection.execute(
            text("SELECT id, price, valid_from, valid_to FROM stock_prices WHERE stock_item_id=:i ORDER BY valid_from"),
            {"i": item_id},
        ).mappings().all()
        for old in existing:
            old_start, old_end = old["valid_from"], old["valid_to"]
            if (old_end is not None and old_end < start) or (end is not None and old_start > end):
                continue
            before = old_start < start
            after = end is not None and (old_end is None or old_end > end)
            if before:
                connection.execute(
                    text("UPDATE stock_prices SET valid_to=:d WHERE id=:id"),
                    {"d": start - timedelta(days=1), "id": old["id"]},
                )
                if after:
                    connection.execute(
                        text(
                            "INSERT INTO stock_prices(stock_item_id,price,valid_from,valid_to,created_by) "
                            "VALUES(:i,:p,:f,:t,:u)"
                        ),
                        {"i": item_id, "p": old["price"], "f": end + timedelta(days=1), "t": old_end, "u": user.username},
                    )
            elif after:
                connection.execute(
                    text("UPDATE stock_prices SET valid_from=:d WHERE id=:id"),
                    {"d": end + timedelta(days=1), "id": old["id"]},
                )
            else:
                connection.execute(text("DELETE FROM stock_prices WHERE id=:id"), {"id": old["id"]})
        new_id = connection.execute(
            text(
                "INSERT INTO stock_prices(stock_item_id,price,valid_from,valid_to,created_by) "
                "VALUES(:i,:p,:f,:t,:u) RETURNING id"
            ),
            {"i": item_id, "p": round(payload.price, 2), "f": start, "t": end, "u": username},
        ).scalar_one()
    return new_id


@app.put("/api/stock/prices/{price_id}")
def update_stock_price(
    price_id: int,
    payload: StockPriceCreate,
    authorization: Optional[str] = Header(default=None),
):
    user = require_admin(authorization)
    if payload.valid_to is not None and payload.valid_to < payload.valid_from:
        raise HTTPException(status_code=422, detail="La date de fin précède la date d’application.")
    with engine.begin() as connection:
        row = connection.execute(
            text("DELETE FROM stock_prices WHERE id=:id RETURNING stock_item_id"), {"id": price_id}
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="Prix introuvable.")
        new_id = _apply_price(connection, row[0], payload, user.username)
    return {"id": new_id}


@app.delete("/api/stock/prices/{price_id}", status_code=204)
def delete_stock_price(price_id: int, authorization: Optional[str] = Header(default=None)):
    require_admin(authorization)
    with engine.begin() as connection:
        deleted = connection.execute(text("DELETE FROM stock_prices WHERE id=:id"), {"id": price_id}).rowcount
    if not deleted:
        raise HTTPException(status_code=404, detail="Prix introuvable.")
    return Response(status_code=204)


STATS_GROUPS = {
    "prestataire_produit": ("COALESCE(NULLIF(trim(d.medecin),''),'(Non renseigné)')", "d.denomination"),
    "prestataire": ("COALESCE(NULLIF(trim(d.medecin),''),'(Non renseigné)')", None),
    "produit": ("d.denomination", None),
    "salle": ("COALESCE(NULLIF(trim(d.salle),''),'(Non renseignée)')", None),
    "mois": ("to_char(d.dispensed_at,'YYYY-MM')", None),
}


PRICE_LOOKUP = (
    "(SELECT sp.price FROM stock_prices sp WHERE sp.stock_item_id=d.stock_item_id "
    "AND sp.valid_from <= d.dispensed_at::date AND (sp.valid_to IS NULL OR sp.valid_to >= d.dispensed_at::date) "
    "ORDER BY sp.valid_from DESC LIMIT 1)"
)
PRICE_JOIN = (
    f"LEFT JOIN LATERAL (SELECT CASE WHEN d.validated_at IS NOT NULL THEN d.validated_price ELSE {PRICE_LOOKUP} END AS price) pr ON true "
)
AMOUNT = "d.quantity * pr.price"


def _stats_where(date_from, date_to, medecin, produit, salle, statut="", allowed=None):
    clauses, params = ["1=1"], {}
    if allowed is not None:
        clauses.append("lower(trim(COALESCE(d.medecin,''))) = ANY(:allowed)")
        params["allowed"] = list(allowed)
    if date_from:
        clauses.append("d.dispensed_at >= :df")
        params["df"] = date_from
    if date_to:
        clauses.append("d.dispensed_at < :dt")
        params["dt"] = date_to + timedelta(days=1)
    if medecin == "__none__":
        clauses.append("COALESCE(trim(d.medecin),'')=''")
    elif medecin:
        clauses.append("lower(trim(d.medecin))=lower(:med)")
        params["med"] = medecin.strip()
    if produit:
        clauses.append("lower(d.denomination) LIKE :prod")
        params["prod"] = f"%{produit.strip().lower()}%"
    if salle:
        clauses.append("lower(COALESCE(d.salle,'')) LIKE :salle")
        params["salle"] = f"%{salle.strip().lower()}%"
    if statut == "validated":
        clauses.append("d.validated_at IS NOT NULL AND d.billed_at IS NULL")
    elif statut == "billed":
        clauses.append("d.billed_at IS NOT NULL")
    elif statut == "pending":
        clauses.append("d.validated_at IS NULL")
    return " AND ".join(clauses), params


@app.get("/api/stats/dispensations")
def stats_dispensations(
    group_by: str = Query(default="prestataire_produit"),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    medecin: str = Query(default="", max_length=255),
    produit: str = Query(default="", max_length=255),
    salle: str = Query(default="", max_length=255),
    statut: str = Query(default="", pattern="^(|validated|billed|pending)$"),
    authorization: Optional[str] = Header(default=None),
):
    user = current_user(authorization)
    scope = _require_stats_scope(user)
    if group_by not in STATS_GROUPS:
        raise HTTPException(status_code=422, detail="Regroupement inconnu.")
    where, params = _stats_where(
        date_from, date_to, medecin, produit, salle, statut, scope["names"] if scope else None
    )
    first, second = STATS_GROUPS[group_by]
    select_cols = f"{first} AS groupe" + (f", {second} AS sous_groupe" if second else "")
    group_cols = first + (f", {second}" if second else "")
    metrics = (
        f", SUM(d.quantity)::int AS quantite, COUNT(*)::int AS delivrances, "
        f"COUNT(DISTINCT d.patient_id)::int AS patients, MAX(d.dispensed_at) AS derniere, "
        f"COALESCE(SUM({AMOUNT}),0)::float AS total, "
        f"COUNT(*) FILTER (WHERE pr.price IS NULL)::int AS sans_prix, "
        f"COUNT(*) FILTER (WHERE d.validated_at IS NOT NULL)::int AS validees, "
        f"COUNT(*) FILTER (WHERE d.billed_at IS NOT NULL)::int AS facturees "
        f"FROM patientdispensation d {PRICE_JOIN}WHERE {where} GROUP BY {group_cols} "
    )
    with engine.connect() as connection:
        groups = connection.execute(
            text(
                f"SELECT {select_cols}{metrics}"
                + ("ORDER BY 1, quantite DESC" if second else "ORDER BY quantite DESC, 1")
            ),
            params,
        ).mappings().all()
        totals = connection.execute(
            text(
                "SELECT COALESCE(SUM(d.quantity),0)::int AS quantite, COUNT(*)::int AS delivrances, "
                "COUNT(DISTINCT d.patient_id)::int AS patients, "
                "COUNT(DISTINCT COALESCE(NULLIF(trim(d.medecin),''),'-'))::int AS prestataires, "
                "COUNT(DISTINCT d.denomination)::int AS produits, "
                f"COALESCE(SUM({AMOUNT}),0)::float AS total, "
                "COUNT(*) FILTER (WHERE pr.price IS NULL)::int AS sans_prix, "
                "COUNT(*) FILTER (WHERE d.validated_at IS NOT NULL)::int AS validees, "
                "COUNT(*) FILTER (WHERE d.billed_at IS NOT NULL)::int AS facturees "
                f"FROM patientdispensation d {PRICE_JOIN}WHERE {where}"
            ),
            params,
        ).mappings().one()
        doctors = [r[0] for r in connection.execute(
            text("SELECT DISTINCT trim(medecin) FROM patientdispensation WHERE COALESCE(trim(medecin),'')<>'' ORDER BY 1")
        )]
        monthly = connection.execute(
            text(
                "SELECT * FROM (SELECT to_char(date_trunc('month', d.dispensed_at), 'YYYY-MM') AS mois, "
                "SUM(d.quantity)::int AS quantite, COUNT(*)::int AS delivrances, "
                f"COALESCE(SUM({AMOUNT}),0)::float AS total "
                f"FROM patientdispensation d {PRICE_JOIN}WHERE {where} GROUP BY 1 ORDER BY 1 DESC LIMIT 12) m ORDER BY mois"
            ),
            params,
        ).mappings().all()
    groups = [dict(row) for row in groups]
    totals = dict(totals)
    monthly = [dict(row) for row in monthly]
    if scope is not None:
        doctors = [name for name in doctors if name.strip().lower() in scope["names"]]
        _mask(groups + [totals] + monthly, scope, ("total", "sans_prix"), ("validees", "facturees"))
    return {
        "group_by": group_by,
        "groups": groups,
        "totals": totals,
        "doctors": doctors,
        "monthly": monthly,
    }


@app.get("/api/stats/dispensations/detail")
def stats_dispensations_detail(
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    medecin: str = Query(default="", max_length=255),
    produit: str = Query(default="", max_length=255),
    salle: str = Query(default="", max_length=255),
    statut: str = Query(default="", pattern="^(|validated|billed|pending)$"),
    authorization: Optional[str] = Header(default=None),
):
    current_user_row = current_user(authorization)
    scope = _require_stats_scope(current_user_row)
    where, params = _stats_where(
        date_from, date_to, medecin, produit, salle, statut, scope["names"] if scope else None
    )
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT d.dispensed_at, COALESCE(NULLIF(trim(d.medecin),''),'') AS prestataire, d.denomination, d.quantity, "
                "d.numero_serie, d.expiration_date, COALESCE(d.salle,'') AS salle, "
                "p.prenom || ' ' || p.nom AS patient, d.dispensed_by, pr.price::float AS prix_unitaire, "
                f"({AMOUNT})::float AS montant, d.validated_at IS NOT NULL AS valide, d.billed_at IS NOT NULL AS facture "
                f"FROM patientdispensation d JOIN patient p ON p.id=d.patient_id {PRICE_JOIN}WHERE {where} "
                "ORDER BY d.dispensed_at DESC LIMIT 20000"
            ),
            params,
        ).mappings().all()
    rows = [dict(row) for row in rows]
    if scope is not None:
        _mask(rows, scope, ("prix_unitaire", "montant"), ("valide", "facture"))
    return rows


def _mask(rows: list, scope: dict, money_keys: tuple, state_keys: tuple) -> None:
    for row in rows:
        if not scope["amounts"]:
            for key in money_keys:
                if key in row:
                    row[key] = None
        if not scope["billing"]:
            for key in state_keys:
                if key in row:
                    row[key] = None


@app.get("/api/stats/rooms/occupancy")
def stats_rooms_occupancy(
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    room: str = Query(default="", max_length=2000),
    authorization: Optional[str] = Header(default=None),
):
    user = current_user(authorization)
    if not has_agenda_access(user):
        raise HTTPException(status_code=403, detail="Accès à l'agenda requis.")
    today = date.today()
    date_to = date_to or today
    date_from = date_from or (date_to - timedelta(days=29))
    if date_from > date_to or (date_to - date_from).days > 731:
        raise HTTPException(status_code=422, detail="Période invalide (730 jours maximum).")
    with engine.connect() as connection:
        rooms = connection.execute(
            text("SELECT id::text AS id, name, color FROM rooms WHERE active=true ORDER BY sort_order, name")
        ).mappings().all()
        all_rooms = [{"id": r["id"], "name": r["name"]} for r in rooms]
        wanted = {part.strip() for part in room.split(",") if part.strip()}
        if wanted:
            rooms = [r for r in rooms if r["id"] in wanted]
        blocks = connection.execute(
            text("SELECT room_id::text AS room_id, weekday, start_time, end_time, valid_from, valid_to "
                 "FROM scheduling_blocks WHERE active=true")
        ).mappings().all()
        opening = connection.execute(
            text("SELECT room_id::text AS room_id, weekday, start_time, end_time, valid_from, valid_to FROM room_opening_slots")
        ).mappings().all()
        appointments = connection.execute(
            text(
                "SELECT room_id::text AS room_id, state::text AS state, "
                "(starts_at AT TIME ZONE 'Europe/Brussels') AS s, (ends_at AT TIME ZONE 'Europe/Brussels') AS e "
                "FROM appointments "
                "WHERE (starts_at AT TIME ZONE 'Europe/Brussels')::date BETWEEN :df AND :dt"
            ),
            {"df": date_from, "dt": date_to},
        ).mappings().all()

    days = [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]

    def open_minutes(slots):
        total = 0
        for slot in slots:
            length = (slot["end_time"].hour * 60 + slot["end_time"].minute) - (slot["start_time"].hour * 60 + slot["start_time"].minute)
            for day in days:
                if day.weekday() != slot["weekday"]:
                    continue
                if slot["valid_from"] and day < slot["valid_from"]:
                    continue
                if slot["valid_to"] and day > slot["valid_to"]:
                    continue
                total += length
        return total

    result = {}
    for r in rooms:
        mine = [b for b in blocks if b["room_id"] == r["id"]] or [o for o in opening if o["room_id"] == r["id"]]
        result[r["id"]] = {
            "room_id": r["id"], "salle": r["name"], "couleur": r["color"],
            "rendez_vous": 0, "annules": 0, "absents": 0,
            "minutes_planifiees": 0, "minutes_ouvertes": open_minutes(mine) if mine else None,
        }
    heat = [[0] * 24 for _ in range(7)]
    for a in appointments:
        entry = result.get(a["room_id"])
        if entry is None:
            continue
        if a["state"] in ("CANCELLED", "POSTPONED"):
            entry["annules"] += 1
            continue
        if a["state"] == "NO_SHOW":
            entry["absents"] += 1
        entry["rendez_vous"] += 1
        start, end = a["s"], a["e"]
        entry["minutes_planifiees"] += int((end - start).total_seconds() // 60)
        cursor = start
        while cursor < end:
            next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            part = min(end, next_hour)
            heat[cursor.weekday()][cursor.hour] += int((part - cursor).total_seconds() // 60)
            cursor = part
    rows = []
    for entry in result.values():
        opened = entry["minutes_ouvertes"]
        entry["taux"] = round(min(entry["minutes_planifiees"] / opened, 9.99) * 100, 1) if opened else None
        rows.append(entry)
    planned = sum(e["minutes_planifiees"] for e in rows)
    opened_total = sum(e["minutes_ouvertes"] or 0 for e in rows if e["minutes_ouvertes"])
    return {
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "salles": rows,
        "heatmap": heat,
        "rooms": all_rooms,
        "totaux": {
            "rendez_vous": sum(e["rendez_vous"] for e in rows),
            "minutes_planifiees": planned,
            "minutes_ouvertes": opened_total,
            "taux": round(planned / opened_total * 100, 1) if opened_total else None,
        },
    }


def _stats_filters(
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    medecin: str = Query(default="", max_length=255),
    produit: str = Query(default="", max_length=255),
    salle: str = Query(default="", max_length=255),
):
    return _stats_where(date_from, date_to, medecin, produit, salle)


@app.post("/api/stats/dispensations/validate")
def validate_dispensations(
    filters=Depends(_stats_filters),
    authorization: Optional[str] = Header(default=None),
):
    user = require_admin(authorization)
    where, params = filters
    with engine.begin() as connection:
        validated = connection.execute(
            text(
                "UPDATE patientdispensation d SET validated_at=now(), validated_by=:u, "
                f"validated_price={PRICE_LOOKUP} "
                f"WHERE d.validated_at IS NULL AND {PRICE_LOOKUP} IS NOT NULL AND {where}"
            ),
            {**params, "u": user.username},
        ).rowcount
        skipped = connection.execute(
            text(f"SELECT COUNT(*) FROM patientdispensation d WHERE d.validated_at IS NULL AND {where}"), params
        ).scalar_one()
    return {"validees": validated, "sans_prix_ignorees": skipped}


@app.post("/api/stats/dispensations/recalculate")
def recalculate_dispensations(
    filters=Depends(_stats_filters),
    authorization: Optional[str] = Header(default=None),
):
    # Les délivrances non validées suivent toujours le tarif en vigueur ; les validées restent figées.
    require_admin(authorization)
    where, params = filters
    with engine.begin() as connection:
        row = connection.execute(
            text(
                "SELECT COUNT(*)::int AS recalculees, "
                "COUNT(*) FILTER (WHERE pr.price IS NULL)::int AS sans_prix, "
                f"COALESCE(SUM({AMOUNT}), 0)::float AS total "
                f"FROM patientdispensation d {PRICE_JOIN}WHERE d.validated_at IS NULL AND {where}"
            ),
            params,
        ).mappings().one()
        figees = connection.execute(
            text(f"SELECT COUNT(*) FROM patientdispensation d WHERE d.validated_at IS NOT NULL AND {where}"), params
        ).scalar_one()
    return {**dict(row), "figees": figees}


@app.post("/api/stats/dispensations/unvalidate")
def unvalidate_dispensations(
    filters=Depends(_stats_filters),
    authorization: Optional[str] = Header(default=None),
):
    require_admin(authorization)
    where, params = filters
    with engine.begin() as connection:
        reopened = connection.execute(
            text(
                "UPDATE patientdispensation d SET validated_at=NULL, validated_by=NULL, validated_price=NULL "
                f"WHERE d.validated_at IS NOT NULL AND d.billed_at IS NULL AND {where}"
            ),
            params,
        ).rowcount
    return {"rouvertes": reopened}


@app.post("/api/stats/dispensations/bill")
def bill_dispensations(
    filters=Depends(_stats_filters),
    authorization: Optional[str] = Header(default=None),
):
    # La facturation est définitive : plus de réouverture ni de recalcul possible.
    user = require_admin(authorization)
    where, params = filters
    with engine.begin() as connection:
        billed = connection.execute(
            text(
                "UPDATE patientdispensation d SET billed_at=now(), billed_by=:u "
                f"WHERE d.validated_at IS NOT NULL AND d.billed_at IS NULL AND {where}"
            ),
            {**params, "u": user.username},
        ).rowcount
        not_validated = connection.execute(
            text(f"SELECT COUNT(*) FROM patientdispensation d WHERE d.validated_at IS NULL AND {where}"), params
        ).scalar_one()
    return {"facturees": billed, "non_validees_ignorees": not_validated}


class StockPriceBulk(StockPriceCreate):
    item_ids: list[int] = Field(min_length=1, max_length=2000)


@app.post("/api/stock/prices/bulk", status_code=201)
def bulk_stock_prices(payload: StockPriceBulk, authorization: Optional[str] = Header(default=None)):
    user = require_admin(authorization)
    if payload.valid_to is not None and payload.valid_to < payload.valid_from:
        raise HTTPException(status_code=422, detail="La date de fin précède la date d’application.")
    single = StockPriceCreate(price=payload.price, valid_from=payload.valid_from, valid_to=payload.valid_to)
    with engine.begin() as connection:
        found = {row[0] for row in connection.execute(
            text("SELECT id FROM stockitem WHERE id = ANY(:ids)"), {"ids": list(set(payload.item_ids))}
        )}
        for item_id in sorted(found):
            _apply_price(connection, item_id, single, user.username)
    return {"mis_a_jour": len(found)}


class DoctorPayload(SQLModel):
    inami6: str = Field(max_length=30)
    inami11: str = Field(max_length=30)
    nom: str = Field(min_length=1, max_length=120)
    prenom: str = Field(min_length=1, max_length=120)
    specialite: Optional[str] = Field(default=None, max_length=120)
    email: Optional[str] = Field(default=None, max_length=255)
    tel: Optional[str] = Field(default=None, max_length=40)
    gsm: Optional[str] = Field(default=None, max_length=40)
    adresse: Optional[str] = Field(default=None, max_length=255)
    code_postal: Optional[str] = Field(default=None, max_length=20)
    ville: Optional[str] = Field(default=None, max_length=120)
    active: bool = True
    resource_id: Optional[str] = None
    stats_access: bool = False
    amounts_access: bool = False
    billing_access: bool = False


class DoctorGroupPayload(SQLModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=255)
    stats_access: bool = False
    amounts_access: bool = False
    billing_access: bool = False
    group_scope: bool = False
    members: list[int] = Field(default_factory=list, max_length=500)


def _check_doctor_link(session: Session, doctor_id: Optional[int], user_id: Optional[int]) -> None:
    if doctor_id is None:
        return
    if session.execute(text("SELECT 1 FROM doctors WHERE id=:i"), {"i": doctor_id}).first() is None:
        raise HTTPException(status_code=422, detail="Médecin introuvable.")
    taken = session.execute(
        text("SELECT username FROM useraccount WHERE doctor_id=:d AND (CAST(:u AS integer) IS NULL OR id<>CAST(:u AS integer))"),
        {"d": doctor_id, "u": user_id},
    ).first()
    if taken:
        raise HTTPException(status_code=409, detail=f"Ce médecin est déjà associé au compte « {taken[0]} ».")


def _clean_text(value: Optional[str]) -> Optional[str]:
    value = " ".join((value or "").split())
    return value or None


def _doctor_values(payload: DoctorPayload) -> dict:
    inami6 = re.sub(r"\D", "", payload.inami6)
    inami11 = re.sub(r"\D", "", payload.inami11)
    if len(inami6) != 6:
        raise HTTPException(status_code=422, detail="Le numéro INAMI court doit comporter 6 chiffres.")
    if len(inami11) != 11:
        raise HTTPException(status_code=422, detail="Le numéro INAMI complet doit comporter 11 chiffres.")
    if not inami11.startswith(inami6):
        raise HTTPException(status_code=422, detail="Le numéro INAMI complet doit commencer par les 6 chiffres du numéro court.")
    email = _clean_text(payload.email)
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="Adresse e-mail invalide.")
    nom, prenom = _clean_text(payload.nom), _clean_text(payload.prenom)
    if not nom or not prenom:
        raise HTTPException(status_code=422, detail="Le nom et le prénom sont requis.")
    return {
        "inami6": inami6, "inami11": inami11, "nom": nom, "prenom": prenom,
        "specialite": _clean_text(payload.specialite), "email": email,
        "tel": _clean_text(payload.tel), "gsm": _clean_text(payload.gsm),
        "adresse": _clean_text(payload.adresse), "code_postal": _clean_text(payload.code_postal),
        "ville": _clean_text(payload.ville), "active": payload.active,
        "stats_access": payload.stats_access, "amounts_access": payload.amounts_access,
        "billing_access": payload.billing_access,
    }


def _integrity_message(error: IntegrityError) -> str:
    detail = str(error.orig)
    if "inami6" in detail:
        return "Ce numéro INAMI court existe déjà."
    if "inami11" in detail:
        return "Ce numéro INAMI complet existe déjà."
    if "resource_id" in detail:
        return "Ce médecin de l'agenda est déjà associé à une autre fiche."
    if "name" in detail:
        return "Un groupe porte déjà ce nom."
    return "Enregistrement impossible : donnée en double."


DOCTOR_COLUMNS = (
    "d.id, d.inami6, d.inami11, d.nom, d.prenom, d.specialite, d.email, d.tel, d.gsm, d.adresse, "
    "d.code_postal, d.ville, d.active, d.resource_id::text AS resource_id, d.stats_access, "
    "d.amounts_access, d.billing_access, r.display_name AS display_name, u.username AS username, "
    "COALESCE((SELECT array_agg(m.group_id ORDER BY m.group_id) FROM doctor_group_members m WHERE m.doctor_id=d.id), '{}') AS groups"
)


def _fetch_doctor(connection, doctor_id: int) -> dict:
    row = connection.execute(
        text(
            f"SELECT {DOCTOR_COLUMNS} FROM doctors d LEFT JOIN people_resources r ON r.id=d.resource_id "
            "LEFT JOIN useraccount u ON u.doctor_id=d.id WHERE d.id=:i"
        ),
        {"i": doctor_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Médecin introuvable.")
    return dict(row)


def _resolve_resource(connection, payload: DoctorPayload, values: dict, create: bool) -> Optional[str]:
    # Le libellé de l'agenda sert de lien avec les délivrances (champ « médecin »).
    if payload.resource_id:
        found = connection.execute(
            text("SELECT id::text FROM people_resources WHERE id::text=:i AND kind='DOCTOR'"), {"i": payload.resource_id}
        ).scalar_one_or_none()
        if found is None:
            raise HTTPException(status_code=422, detail="Médecin de l'agenda introuvable.")
        return found
    if not create:
        return None
    return connection.execute(
        text(
            "INSERT INTO people_resources(kind,display_name) VALUES('DOCTOR',:n) "
            "ON CONFLICT (kind,display_name) DO UPDATE SET active=true RETURNING id::text"
        ),
        {"n": f"Dr {values['prenom']} {values['nom']}"},
    ).scalar_one()


@app.get("/api/admin/doctors")
def admin_list_doctors(authorization: Optional[str] = Header(default=None)):
    require_admin(authorization)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                f"SELECT {DOCTOR_COLUMNS} FROM doctors d LEFT JOIN people_resources r ON r.id=d.resource_id "
                "LEFT JOIN useraccount u ON u.doctor_id=d.id ORDER BY lower(d.nom), lower(d.prenom)"
            )
        ).mappings().all()
        resources = connection.execute(
            text("SELECT id::text AS id, display_name FROM people_resources WHERE kind='DOCTOR' AND active=true ORDER BY display_name")
        ).mappings().all()
    return {"doctors": [dict(row) for row in rows], "resources": [dict(row) for row in resources]}


@app.post("/api/admin/doctors", status_code=201)
def admin_create_doctor(payload: DoctorPayload, authorization: Optional[str] = Header(default=None)):
    require_admin(authorization)
    values = _doctor_values(payload)
    try:
        with engine.begin() as connection:
            values["resource_id"] = _resolve_resource(connection, payload, values, create=True)
            doctor_id = connection.execute(
                text(
                    "INSERT INTO doctors(inami6,inami11,nom,prenom,specialite,email,tel,gsm,adresse,code_postal,ville,"
                    "active,resource_id,stats_access,amounts_access,billing_access) VALUES(:inami6,:inami11,:nom,:prenom,"
                    ":specialite,:email,:tel,:gsm,:adresse,:code_postal,:ville,:active,CAST(:resource_id AS uuid),"
                    ":stats_access,:amounts_access,:billing_access) RETURNING id"
                ),
                values,
            ).scalar_one()
            return _fetch_doctor(connection, doctor_id)
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail=_integrity_message(error)) from error


@app.put("/api/admin/doctors/{doctor_id}")
def admin_update_doctor(doctor_id: int, payload: DoctorPayload, authorization: Optional[str] = Header(default=None)):
    require_admin(authorization)
    values = _doctor_values(payload)
    try:
        with engine.begin() as connection:
            _fetch_doctor(connection, doctor_id)
            resource = _resolve_resource(connection, payload, values, create=False)
            sets = (
                "inami6=:inami6, inami11=:inami11, nom=:nom, prenom=:prenom, specialite=:specialite, email=:email, "
                "tel=:tel, gsm=:gsm, adresse=:adresse, code_postal=:code_postal, ville=:ville, active=:active, "
                "stats_access=:stats_access, amounts_access=:amounts_access, billing_access=:billing_access"
            )
            if resource:
                sets += ", resource_id=CAST(:resource_id AS uuid)"
            values.update({"resource_id": resource, "id": doctor_id})
            connection.execute(text(f"UPDATE doctors SET {sets} WHERE id=:id"), values)
            return _fetch_doctor(connection, doctor_id)
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail=_integrity_message(error)) from error


@app.delete("/api/admin/doctors/{doctor_id}")
def admin_delete_doctor(doctor_id: int, authorization: Optional[str] = Header(default=None)):
    require_admin(authorization)
    with engine.begin() as connection:
        linked = connection.execute(text("SELECT username FROM useraccount WHERE doctor_id=:i"), {"i": doctor_id}).first()
        if linked:
            raise HTTPException(status_code=409, detail=f"Ce médecin est associé au compte « {linked[0]} ». Dissociez-le d'abord.")
        if connection.execute(text("DELETE FROM doctors WHERE id=:i"), {"i": doctor_id}).rowcount != 1:
            raise HTTPException(status_code=404, detail="Médecin introuvable.")
    return {"success": True}


def _fetch_group(connection, group_id: int) -> dict:
    row = connection.execute(
        text(
            "SELECT g.id, g.name, g.description, g.stats_access, g.amounts_access, g.billing_access, g.group_scope, "
            "COALESCE((SELECT array_agg(m.doctor_id ORDER BY m.doctor_id) FROM doctor_group_members m WHERE m.group_id=g.id), '{}') AS members "
            "FROM doctor_groups g WHERE g.id=:i"
        ),
        {"i": group_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Groupe introuvable.")
    return dict(row)


def _save_group_members(connection, group_id: int, members: list[int]) -> None:
    ids = sorted(set(members))
    connection.execute(text("DELETE FROM doctor_group_members WHERE group_id=:g"), {"g": group_id})
    if ids:
        found = {r[0] for r in connection.execute(text("SELECT id FROM doctors WHERE id = ANY(:ids)"), {"ids": ids})}
        if found != set(ids):
            raise HTTPException(status_code=422, detail="Un des médecins sélectionnés est introuvable.")
        for doctor_id in ids:
            connection.execute(
                text("INSERT INTO doctor_group_members(group_id,doctor_id) VALUES(:g,:d)"), {"g": group_id, "d": doctor_id}
            )


@app.get("/api/admin/doctor-groups")
def admin_list_doctor_groups(authorization: Optional[str] = Header(default=None)):
    require_admin(authorization)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT g.id, g.name, g.description, g.stats_access, g.amounts_access, g.billing_access, g.group_scope, "
                "COALESCE((SELECT array_agg(m.doctor_id ORDER BY m.doctor_id) FROM doctor_group_members m WHERE m.group_id=g.id), '{}') AS members "
                "FROM doctor_groups g ORDER BY lower(g.name)"
            )
        ).mappings().all()
    return [dict(row) for row in rows]


@app.post("/api/admin/doctor-groups", status_code=201)
def admin_create_doctor_group(payload: DoctorGroupPayload, authorization: Optional[str] = Header(default=None)):
    require_admin(authorization)
    name = _clean_text(payload.name)
    if not name:
        raise HTTPException(status_code=422, detail="Le nom du groupe est requis.")
    try:
        with engine.begin() as connection:
            group_id = connection.execute(
                text(
                    "INSERT INTO doctor_groups(name,description,stats_access,amounts_access,billing_access,group_scope) "
                    "VALUES(:name,:description,:stats_access,:amounts_access,:billing_access,:group_scope) RETURNING id"
                ),
                {**payload.model_dump(exclude={"members"}), "name": name, "description": _clean_text(payload.description)},
            ).scalar_one()
            _save_group_members(connection, group_id, payload.members)
            return _fetch_group(connection, group_id)
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail=_integrity_message(error)) from error


@app.put("/api/admin/doctor-groups/{group_id}")
def admin_update_doctor_group(group_id: int, payload: DoctorGroupPayload, authorization: Optional[str] = Header(default=None)):
    require_admin(authorization)
    name = _clean_text(payload.name)
    if not name:
        raise HTTPException(status_code=422, detail="Le nom du groupe est requis.")
    try:
        with engine.begin() as connection:
            _fetch_group(connection, group_id)
            connection.execute(
                text(
                    "UPDATE doctor_groups SET name=:name, description=:description, stats_access=:stats_access, "
                    "amounts_access=:amounts_access, billing_access=:billing_access, group_scope=:group_scope WHERE id=:id"
                ),
                {**payload.model_dump(exclude={"members"}), "name": name, "description": _clean_text(payload.description), "id": group_id},
            )
            _save_group_members(connection, group_id, payload.members)
            return _fetch_group(connection, group_id)
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail=_integrity_message(error)) from error


@app.delete("/api/admin/doctor-groups/{group_id}")
def admin_delete_doctor_group(group_id: int, authorization: Optional[str] = Header(default=None)):
    require_admin(authorization)
    with engine.begin() as connection:
        if connection.execute(text("DELETE FROM doctor_groups WHERE id=:i"), {"i": group_id}).rowcount != 1:
            raise HTTPException(status_code=404, detail="Groupe introuvable.")
    return {"success": True}


def _stats_scope(user: UserAccount) -> Optional[dict]:
    """None : aucune restriction (administrateur ou compte non associé à un médecin)."""
    if user.role != "user" or not user.doctor_id:
        return None
    rights = {"stats": False, "amounts": False, "billing": False, "names": set()}
    with engine.connect() as connection:
        doctor = connection.execute(
            text(
                "SELECT d.stats_access, d.amounts_access, d.billing_access, r.display_name FROM doctors d "
                "LEFT JOIN people_resources r ON r.id=d.resource_id WHERE d.id=:i AND d.active"
            ),
            {"i": user.doctor_id},
        ).mappings().first()
        if doctor is None:
            return rights
        rights.update(stats=doctor["stats_access"], amounts=doctor["amounts_access"], billing=doctor["billing_access"])
        own_name = (doctor["display_name"] or "").strip().lower()
        groups = connection.execute(
            text(
                "SELECT g.id, g.stats_access, g.amounts_access, g.billing_access, g.group_scope FROM doctor_groups g "
                "JOIN doctor_group_members m ON m.group_id=g.id WHERE m.doctor_id=:i"
            ),
            {"i": user.doctor_id},
        ).mappings().all()
        for group in groups:
            rights["stats"] = rights["stats"] or group["stats_access"]
            rights["amounts"] = rights["amounts"] or group["amounts_access"]
            rights["billing"] = rights["billing"] or group["billing_access"]
            if group["group_scope"] and group["stats_access"]:
                rights["names"].update(
                    (r[0] or "").strip().lower()
                    for r in connection.execute(
                        text(
                            "SELECT r.display_name FROM doctor_group_members m JOIN doctors dd ON dd.id=m.doctor_id AND dd.active "
                            "JOIN people_resources r ON r.id=dd.resource_id WHERE m.group_id=:g"
                        ),
                        {"g": group["id"]},
                    )
                )
    if own_name:
        rights["names"].add(own_name)
    rights["names"].discard("")
    rights["names"] = sorted(rights["names"])
    return rights


def _require_stats_scope(user: UserAccount) -> Optional[dict]:
    scope = _stats_scope(user)
    if scope is not None and not scope["stats"]:
        raise HTTPException(status_code=403, detail="Votre profil médecin n'a pas accès aux statistiques.")
    return scope


@app.get("/api/stats/rights")
def stats_rights(authorization: Optional[str] = Header(default=None)):
    scope = _stats_scope(current_user(authorization))
    if scope is None:
        return {"restricted": False, "stats": True, "amounts": True, "billing": True}
    return {"restricted": True, "stats": scope["stats"], "amounts": scope["amounts"], "billing": scope["billing"]}


@app.get("/api/stock/doctors")
def list_stock_doctors(authorization: Optional[str] = Header(default=None)):
    current_user(authorization)
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT id::text AS id, display_name FROM people_resources WHERE kind='DOCTOR' AND active=true ORDER BY display_name")
        ).mappings().all()
    return [dict(row) for row in rows]


@app.post("/api/stock/doctors", status_code=201)
def add_stock_doctor(payload: dict, authorization: Optional[str] = Header(default=None)):
    current_user(authorization)
    name = " ".join(str(payload.get("display_name", "")).split())
    if not name or len(name) > 255:
        raise HTTPException(status_code=422, detail="Le nom du médecin est requis (255 caractères maximum).")
    with engine.begin() as connection:
        row = connection.execute(
            text(
                "INSERT INTO people_resources(kind,display_name) VALUES('DOCTOR',:name) "
                "ON CONFLICT (kind,display_name) DO UPDATE SET active=true RETURNING id::text AS id, display_name"
            ),
            {"name": name},
        ).mappings().one()
    return dict(row)


@app.get("/api/stock/search")
def search_stock_for_dispense(
    q: str = Query(default="", max_length=100),
    authorization: Optional[str] = Header(default=None),
):
    current_user(authorization)
    term = q.strip()
    if len(term) < 2:
        return []
    pattern = f"%{term.lower()}%"
    results: list[dict] = []
    with Session(engine) as session:
        items = session.exec(
            select(StockItem).where(
                func.lower(StockItem.denomination).like(pattern)
                | func.lower(func.coalesce(StockItem.code_barres, "")).like(pattern)
                | func.lower(func.coalesce(StockItem.numero_serie, "")).like(pattern)
            ).order_by(StockItem.denomination).limit(20)
        ).all()
        for item in items:
            code = (item.code_barres or item.numero_serie or "").strip()
            if code and item.quantity > 0:
                results.append({
                    "denomination": item.denomination, "code": code, "quantity": item.quantity,
                    "numero_serie": item.numero_serie, "numero_lot": item.numero_lot, "unit": False,
                })
        units = session.exec(
            select(StockQrUnit, StockItem).join(StockItem, StockItem.id == StockQrUnit.stock_item_id).where(
                (func.lower(StockQrUnit.numero_serie).like(pattern)
                 | func.lower(StockItem.denomination).like(pattern)
                 | func.lower(func.coalesce(StockItem.code_barres, "")).like(pattern))
                & (StockQrUnit.quantity > 0)
            ).order_by(StockItem.denomination, StockQrUnit.numero_serie).limit(30)
        ).all()
        for unit, item in units:
            results.append({
                "denomination": item.denomination, "code": unit.numero_serie.strip(), "quantity": unit.quantity,
                "numero_serie": unit.numero_serie, "numero_lot": unit.numero_lot, "unit": True,
            })
    return results[:40]


@app.get("/api/stock", response_model=list[StockItemRead])
def list_stock_items(authorization: Optional[str] = Header(default=None)):
    current_user(authorization)
    with Session(engine) as session:
        statement = select(StockItem).order_by(
            StockItem.entry_date.desc(),
            StockItem.id.desc(),
        )
        return list(session.exec(statement).all())


@app.post("/api/stock", response_model=StockItemRead, status_code=201)
def create_stock_item(
    payload: StockItemCreate,
    authorization: Optional[str] = Header(default=None),
):
    actor = require_admin(authorization)
    denomination = payload.denomination.strip()
    if not denomination:
        raise HTTPException(status_code=422, detail="La dénomination est requise.")
    item = StockItem.model_validate(
        payload.model_dump() | {"denomination": denomination}
    )
    with Session(engine) as session:
        if item.code_barres is not None:
            item.code_barres = item.code_barres.strip() or None
        if item.numero_serie is not None:
            item.numero_serie = item.numero_serie.strip() or None
        if item.code_barres and item.code_barres == item.numero_serie:
            raise HTTPException(
                status_code=409,
                detail="Le code-barres et le numéro de série doivent être différents.",
            )
        ensure_unique_product_code(session, item.code_barres)
        ensure_unique_stock_serial(session, item.numero_serie)
        session.add(item)
        session.flush()
        if item.id is None:
            raise RuntimeError("L'identifiant de l'article n'a pas été généré.")
        record_audit_event(
            session,
            "article de stock",
            item.id,
            "création",
            actor.username,
            None,
            audit_snapshot(item),
        )
        session.commit()
        session.refresh(item)
        return item


LOCK_STATE_SQL = (
    "CASE WHEN bool_or(d.billed_at IS NOT NULL) THEN 'billed' ELSE 'validated' END"
)


def _lock_state(session: Session, item_id: Optional[int] = None, serial: Optional[str] = None) -> Optional[str]:
    # Un produit ou implant lié à une délivrance validée ou facturée est verrouillé.
    clause, params = ("d.stock_item_id=:i", {"i": item_id}) if item_id is not None else (
        "trim(d.numero_serie)=:s", {"s": (serial or "").strip()}
    )
    return session.execute(
        text(f"SELECT {LOCK_STATE_SQL} FROM patientdispensation d WHERE d.validated_at IS NOT NULL AND {clause}"),
        params,
    ).scalar_one_or_none()


def _ensure_not_locked(session: Session, item_id: Optional[int] = None, serial: Optional[str] = None) -> None:
    state = _lock_state(session, item_id, serial)
    if state:
        label = "facturée(s)" if state == "billed" else "validée(s)"
        raise HTTPException(
            status_code=409,
            detail=f"Ce produit est lié à des délivrances {label} : il ne peut plus être modifié ni supprimé."
            + ("" if state == "billed" else " Rouvrez d'abord ces délivrances dans les statistiques."),
        )


@app.get("/api/stock/locked")
def stock_locks(authorization: Optional[str] = Header(default=None)):
    current_user(authorization)
    with engine.connect() as connection:
        items = connection.execute(
            text(
                f"SELECT d.stock_item_id AS k, {LOCK_STATE_SQL} AS state FROM patientdispensation d "
                "WHERE d.validated_at IS NOT NULL GROUP BY d.stock_item_id"
            )
        ).all()
        serials = connection.execute(
            text(
                f"SELECT trim(d.numero_serie) AS k, {LOCK_STATE_SQL} AS state FROM patientdispensation d "
                "WHERE d.validated_at IS NOT NULL AND COALESCE(trim(d.numero_serie),'')<>'' GROUP BY trim(d.numero_serie)"
            )
        ).all()
    return {"items": {str(k): s for k, s in items}, "serials": {k: s for k, s in serials}}


@app.patch("/api/stock/{item_id}", response_model=StockItemRead)
def update_stock_item(
    item_id: int,
    payload: StockItemUpdate,
    authorization: Optional[str] = Header(default=None),
):
    actor = require_admin(authorization)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Aucune donnée à modifier.")
    if "denomination" in changes:
        if changes["denomination"] is None or not changes["denomination"].strip():
            raise HTTPException(status_code=422, detail="La dénomination est requise.")
        changes["denomination"] = changes["denomination"].strip()
    if "quantity" in changes and changes["quantity"] is None:
        raise HTTPException(status_code=422, detail="La quantité est requise.")
    if "min_quantity" in changes and changes["min_quantity"] is None:
        raise HTTPException(status_code=422, detail="Le seuil minimum est requis.")
    if "entry_date" in changes and changes["entry_date"] is None:
        raise HTTPException(status_code=422, detail="La date d'entrée est requise.")
    if "code_barres" in changes:
        changes["code_barres"] = (changes["code_barres"] or "").strip() or None
    with Session(engine) as session:
        item = session.get(StockItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Article introuvable.")
        before_data = audit_snapshot(item)
        next_barcode = changes.get("code_barres", item.code_barres)
        next_serial = changes.get("numero_serie", item.numero_serie)
        if next_barcode is not None:
            next_barcode = next_barcode.strip() or None
            changes["code_barres"] = next_barcode
        if next_serial is not None:
            next_serial = next_serial.strip() or None
            if "numero_serie" in changes:
                changes["numero_serie"] = next_serial
        if next_barcode and next_barcode == next_serial:
            raise HTTPException(
                status_code=409,
                detail="Le code-barres et le numéro de série doivent être différents.",
            )
        tracked_quantity = session.exec(
            select(func.coalesce(func.sum(StockQrUnit.quantity), 0)).where(
                StockQrUnit.stock_item_id == item_id
            )
        ).one()
        if "quantity" in changes and changes["quantity"] < tracked_quantity:
            raise HTTPException(
                status_code=409,
                detail=f"La quantité ne peut pas être inférieure aux sous-entités QR encore en stock ({tracked_quantity}).",
            )
        if "code_barres" in changes:
            ensure_unique_product_code(session, changes["code_barres"], item_id)
        if "numero_serie" in changes:
            if changes["numero_serie"] is not None:
                changes["numero_serie"] = changes["numero_serie"].strip() or None
            ensure_unique_stock_serial(session, changes["numero_serie"], item_id)
        for field, value in changes.items():
            setattr(item, field, value)
        session.add(item)
        record_audit_event(
            session,
            "article de stock",
            item.id,
            "modification",
            actor.username,
            before_data,
            audit_snapshot(item),
        )
        session.commit()
        session.refresh(item)
        return item


@app.delete("/api/stock/{item_id}")
def delete_stock_item(
    item_id: int,
    authorization: Optional[str] = Header(default=None),
):
    actor = require_admin(authorization)
    with Session(engine) as session:
        item = session.get(StockItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Article introuvable.")
        _ensure_not_locked(session, item_id)
        has_qr_units = session.exec(
            select(StockQrUnit.id)
            .where(StockQrUnit.stock_item_id == item_id)
            .limit(1)
        ).first()
        if has_qr_units:
            raise HTTPException(
                status_code=409,
                detail="Cet article possède des sous-entités QR. Supprimez-les d'abord depuis le tableau des sous-entités.",
            )
        record_audit_event(
            session,
            "article de stock",
            item.id,
            "suppression",
            actor.username,
            audit_snapshot(item),
            None,
        )
        session.delete(item)
        session.commit()
    return {"success": True}


def ensure_unique_stock_serial(
    session: Session,
    serial: Optional[str],
    exclude_item_id: Optional[int] = None,
) -> None:
    if not serial:
        return
    statement = select(StockItem.id).where(func.trim(StockItem.numero_serie) == serial.strip())
    if exclude_item_id is not None:
        statement = statement.where(StockItem.id != exclude_item_id)
    if session.exec(statement).first() is not None:
        raise HTTPException(
            status_code=409,
            detail="Ce numéro de série est déjà associé à un article du stock.",
        )
    barcode_statement = select(StockItem.id).where(
        func.trim(StockItem.code_barres) == serial.strip()
    )
    if exclude_item_id is not None:
        barcode_statement = barcode_statement.where(StockItem.id != exclude_item_id)
    if session.exec(barcode_statement).first() is not None:
        raise HTTPException(
            status_code=409,
            detail="Ce numéro de série est déjà utilisé comme code-barres d'un article.",
        )
    unit_statement = select(StockQrUnit.id).where(
        func.trim(StockQrUnit.numero_serie) == serial.strip()
    )
    if session.exec(unit_statement).first() is not None:
        raise HTTPException(
            status_code=409,
            detail="Ce numéro de série est déjà associé à une sous-entité QR.",
        )


def ensure_unique_product_code(
    session: Session,
    code: Optional[str],
    exclude_item_id: Optional[int] = None,
) -> None:
    if not code:
        return
    normalized_code = code.strip()
    item_statement = select(StockItem.id).where(
        (func.trim(StockItem.code_barres) == normalized_code)
        | (func.trim(StockItem.numero_serie) == normalized_code)
    )
    if exclude_item_id is not None:
        item_statement = item_statement.where(StockItem.id != exclude_item_id)
    if session.exec(item_statement).first() is not None:
        raise HTTPException(
            status_code=409,
            detail="Ce code est déjà associé à un article du stock.",
        )
    unit_statement = select(StockQrUnit.id).where(
        func.trim(StockQrUnit.numero_serie) == normalized_code
    )
    if session.exec(unit_statement).first() is not None:
        raise HTTPException(
            status_code=409,
            detail="Ce code est déjà associé à une sous-entité QR.",
        )


@app.post("/api/stock/{item_id}/withdraw", response_model=StockWithdrawalRead, status_code=201)
def withdraw_stock(
    item_id: int,
    payload: StockWithdrawalRequest,
    authorization: Optional[str] = Header(default=None),
):
    user = current_user(authorization)
    with Session(engine) as session:
        item = session.get(StockItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Article introuvable.")
        item_before = audit_snapshot(item)
        qr_units_before = {
            unit.id: audit_snapshot(unit)
            for unit in session.exec(
                select(StockQrUnit).where(StockQrUnit.stock_item_id == item_id)
            ).all()
        }
        decrement_stock(session, item, payload.quantity)
        withdrawal = StockWithdrawal(
            stock_item_id=item_id,
            denomination=item.denomination,
            quantity=payload.quantity,
            withdrawn_by=user.username,
        )
        session.add(withdrawal)
        session.flush()
        if withdrawal.id is None:
            raise RuntimeError("L'identifiant de la sortie de stock n'a pas été généré.")
        record_audit_event(
            session,
            "article de stock",
            item.id,
            "modification",
            user.username,
            item_before,
            audit_snapshot(item),
        )
        for unit_id, before_data in qr_units_before.items():
            unit = session.get(StockQrUnit, unit_id)
            if unit and before_data != audit_snapshot(unit):
                record_audit_event(
                    session,
                    "sous-entité QR",
                    unit_id,
                    "modification",
                    user.username,
                    before_data,
                    audit_snapshot(unit),
                )
        record_audit_event(
            session,
            "sortie de stock",
            withdrawal.id,
            "création",
            user.username,
            None,
            audit_snapshot(withdrawal),
        )
        session.commit()
        session.refresh(withdrawal)
        return withdrawal


def decrement_stock(
    session: Session,
    item: StockItem,
    quantity: int,
    qr_unit_id: Optional[int] = None,
) -> None:
    original_quantity = item.quantity
    qr_quantity = session.exec(
        select(func.coalesce(func.sum(StockQrUnit.quantity), 0)).where(
            StockQrUnit.stock_item_id == item.id
        )
    ).one()
    legacy_quantity = max(original_quantity - qr_quantity, 0)
    if qr_unit_id is not None:
        qr_unit = session.get(StockQrUnit, qr_unit_id)
        if (
            not qr_unit
            or qr_unit.stock_item_id != item.id
            or qr_unit.quantity < quantity
        ):
            raise HTTPException(
                status_code=409,
                detail="Cette sous-entité QR n'est plus disponible. Scannez à nouveau le produit.",
            )
        legacy_quantity = 0

    result = session.exec(
        update(StockItem)
        .where(StockItem.id == item.id, StockItem.quantity >= quantity)
        .values(quantity=StockItem.quantity - quantity)
    )
    if result.rowcount != 1:
        raise HTTPException(
            status_code=409,
            detail=f"Stock insuffisant. Quantité disponible : {item.quantity}.",
        )

    if qr_unit_id is not None:
        unit_result = session.exec(
            update(StockQrUnit)
            .where(
                StockQrUnit.id == qr_unit_id,
                StockQrUnit.stock_item_id == item.id,
                StockQrUnit.quantity >= quantity,
            )
            .values(quantity=StockQrUnit.quantity - quantity)
        )
        if unit_result.rowcount != 1:
            raise HTTPException(
                status_code=409,
                detail="Cette sous-entité QR vient d'être délivrée. Scannez à nouveau le produit.",
            )
    else:
        remaining_to_withdraw = max(quantity - legacy_quantity, 0)
        if remaining_to_withdraw:
            qr_units = session.exec(
                select(StockQrUnit)
                .where(
                    StockQrUnit.stock_item_id == item.id,
                    StockQrUnit.quantity > 0,
                )
                .order_by(StockQrUnit.created_at, StockQrUnit.id)
            ).all()
            for qr_unit in qr_units:
                withdrawn_from_unit = min(qr_unit.quantity, remaining_to_withdraw)
                qr_unit.quantity -= withdrawn_from_unit
                remaining_to_withdraw -= withdrawn_from_unit
                session.add(qr_unit)
                if not remaining_to_withdraw:
                    break
            if remaining_to_withdraw:
                raise HTTPException(
                    status_code=409,
                    detail="Les sous-entités QR disponibles ne correspondent plus à la quantité agrégée. Actualisez le stock et réessayez.",
                )
    session.refresh(item)


@app.post("/api/stock/resolve-scan", response_model=StockScanMatch)
def resolve_stock_scan(
    payload: StockScanResolveRequest,
    authorization: Optional[str] = Header(default=None),
):
    current_user(authorization)
    code = payload.code.strip()
    if not code:
        raise HTTPException(status_code=422, detail="Le code scanné est requis.")
    with Session(engine) as session:
        qr_units = session.exec(
            select(StockQrUnit).where(func.trim(StockQrUnit.numero_serie) == code)
        ).all()
        products = session.exec(
            select(StockItem).where(
                (func.trim(StockItem.code_barres) == code)
                | (func.trim(StockItem.numero_serie) == code)
            )
        ).all()
        if len(qr_units) + len(products) > 1:
            raise HTTPException(
                status_code=409,
                detail="Ce code correspond à plusieurs références du stock. Corrigez les codes dans la gestion de stock.",
            )
        if qr_units:
            unit = qr_units[0]
            item = session.get(StockItem, unit.stock_item_id)
            if not item:
                raise HTTPException(
                    status_code=409,
                    detail="La sous-entité QR ne référence plus un article existant.",
                )
            return StockScanMatch(
                stock_item_id=item.id,
                denomination=item.denomination,
                quantity=min(item.quantity, unit.quantity),
                scanned_code=code,
                qr_unit_id=unit.id,
                numero_serie=unit.numero_serie,
                expiration_date=unit.expiration_date,
                numero_lot=unit.numero_lot,
            )
        if products:
            item = products[0]
            return StockScanMatch(
                stock_item_id=item.id,
                denomination=item.denomination,
                quantity=item.quantity,
                scanned_code=code,
                numero_serie=item.numero_serie,
                expiration_date=item.expiration_date,
                numero_lot=item.numero_lot,
            )
        raise HTTPException(
            status_code=404,
            detail="Code inconnu du stock. Ajoutez-le à la fiche produit ou scannez un QR enregistré.",
        )


@app.get("/api/patients", response_model=list[PatientRead])
def list_patients(authorization: Optional[str] = Header(default=None)):
    current_user(authorization)
    with Session(engine) as session:
        return list(
            session.exec(
                select(Patient).order_by(Patient.nom, Patient.prenom, Patient.id)
            ).all()
        )


@app.get("/api/patients/{patient_id}/photo")
def get_patient_photo(patient_id: int, authorization: Optional[str] = Header(default=None)):
    current_user(authorization)
    with engine.connect() as connection:
        photo = connection.execute(
            text("SELECT photo FROM patient WHERE id=:id"), {"id": patient_id}
        ).scalar()
    if not photo:
        raise HTTPException(status_code=404, detail="Aucune photo.")
    return Response(
        content=bytes(photo),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store"},
    )


@app.put("/api/patients/{patient_id}/photo")
def put_patient_photo(
    patient_id: int,
    payload: PatientPhotoPayload,
    authorization: Optional[str] = Header(default=None),
):
    current_user(authorization)
    raw = decode_photo(payload.photo)
    with engine.begin() as connection:
        updated = connection.execute(
            text("UPDATE patient SET photo=:photo WHERE id=:id RETURNING id"),
            {"photo": raw, "id": patient_id},
        ).first()
    if not updated:
        raise HTTPException(status_code=404, detail="Patient introuvable.")
    return {"id": patient_id}


@app.patch("/api/patients/{patient_id}", response_model=PatientRead)
def update_patient(
    patient_id: int,
    payload: PatientUpdate,
    authorization: Optional[str] = Header(default=None),
):
    user = current_user(authorization)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Aucune donnée à modifier.")
    for field in ("nom", "prenom"):
        if field in changes:
            value = changes[field]
            if value is None or not value.strip():
                raise HTTPException(
                    status_code=422,
                    detail="Le nom et le prénom ne peuvent pas être vides.",
                )
            changes[field] = value.strip()
    if "date_naissance" in changes:
        if changes["date_naissance"] is None:
            raise HTTPException(
                status_code=422,
                detail="La date de naissance est requise.",
            )
        if changes["date_naissance"] > date.today():
            raise HTTPException(
                status_code=422,
                detail="La date de naissance ne peut pas être dans le futur.",
            )
    for field in ("prenoms", "lieu_naissance", "nationalite", "adresse", "code_postal", "commune"):
        if field in changes:
            changes[field] = " ".join((changes[field] or "").split()) or None
    if "numero_national" in changes:
        changes["numero_national"] = normalize_national_number(changes["numero_national"])
    if "sexe" in changes:
        changes["sexe"] = (changes["sexe"] or "").strip().upper() or None
        if changes["sexe"] not in (None, "M", "F", "X"):
            raise HTTPException(status_code=422, detail="Le sexe doit être M, F ou X.")
    for field in ("mobile", "email", "language"):
        if field in changes:
            value = (changes[field] or "").strip()
            changes[field] = value or None
    if changes.get("mobile") and not PATIENT_MOBILE_PATTERN.match(changes["mobile"]):
        raise HTTPException(status_code=422, detail="Le numéro de mobile est invalide.")
    if changes.get("email"):
        changes["email"] = changes["email"].lower()
        if not PATIENT_EMAIL_PATTERN.match(changes["email"]):
            raise HTTPException(status_code=422, detail="L’adresse e-mail est invalide.")
    if changes.get("language"):
        changes["language"] = changes["language"].lower()
        if changes["language"] not in PATIENT_LANGUAGES:
            raise HTTPException(status_code=422, detail="La langue doit être fr, nl, de ou en.")
    for field in ("sms_consent", "email_consent"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail="Le consentement doit être vrai ou faux.")
    with Session(engine) as session:
        patient = session.get(Patient, patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient introuvable.")
        if changes.get("sms_consent") and not (changes.get("mobile", patient.mobile)):
            raise HTTPException(
                status_code=422,
                detail="Un numéro de mobile est requis pour autoriser les SMS.",
            )
        if changes.get("email_consent") and not (changes.get("email", patient.email)):
            raise HTTPException(
                status_code=422,
                detail="Une adresse e-mail est requise pour autoriser les e-mails.",
            )
        if "mobile" in changes and not changes["mobile"]:
            changes["sms_consent"] = False
        if "email" in changes and not changes["email"]:
            changes["email_consent"] = False
        before_data = audit_snapshot(patient)
        for field, value in changes.items():
            setattr(patient, field, value)
        session.add(patient)
        record_audit_event(
            session,
            "patient",
            patient.id,
            "modification",
            user.username,
            before_data,
            audit_snapshot(patient),
        )
        session.commit()
        session.refresh(patient)
        return patient


def patient_dispensation_read(
    session: Session,
    dispensation: PatientDispensation,
) -> PatientDispensationRead:
    patient = session.get(Patient, dispensation.patient_id)
    item = session.get(StockItem, dispensation.stock_item_id)
    if not patient or not item:
        raise RuntimeError("Une délivrance référence un patient ou un article introuvable.")
    return PatientDispensationRead(
        id=dispensation.id,
        patient_id=patient.id,
        stock_item_id=item.id,
        withdrawal_id=dispensation.withdrawal_id,
        patient_name=f"{patient.prenom} {patient.nom}",
        patient_date_naissance=patient.date_naissance,
        denomination=dispensation.denomination,
        quantity=dispensation.quantity,
        scanned_code=dispensation.scanned_code,
        numero_serie=dispensation.numero_serie,
        expiration_date=dispensation.expiration_date,
        medecin=dispensation.medecin,
        salle=dispensation.salle,
        dispensed_by=dispensation.dispensed_by,
        dispensed_at=dispensation.dispensed_at,
        stock_remaining=item.quantity,
    )


@app.post(
    "/api/stock/{item_id}/dispense",
    response_model=PatientDispensationRead,
    status_code=201,
)
def dispense_stock_to_patient(
    item_id: int,
    payload: StockDispenseRequest,
    authorization: Optional[str] = Header(default=None),
):
    user = current_user(authorization)
    code = payload.scanned_code.strip()
    medecin = (payload.medecin or "").strip() or None
    salle = (payload.salle or "").strip() or None
    if not code:
        raise HTTPException(status_code=422, detail="Le code du produit est requis.")
    if not medecin:
        raise HTTPException(status_code=422, detail="Le médecin est obligatoire.")
    if payload.patient_id is None:
        nom = (payload.nom or "").strip()
        prenom = (payload.prenom or "").strip()
        if not nom or not prenom or payload.date_naissance is None:
            raise HTTPException(
                status_code=422,
                detail="Le nom, le prénom et la date de naissance du nouveau patient sont requis.",
            )
        if payload.date_naissance > date.today():
            raise HTTPException(
                status_code=422,
                detail="La date de naissance ne peut pas être dans le futur.",
            )
    elif payload.nom is not None or payload.prenom is not None or payload.date_naissance is not None:
        raise HTTPException(
            status_code=422,
            detail="Choisissez un patient existant ou renseignez un nouveau patient, pas les deux.",
        )

    with Session(engine) as session:
        patient_created = payload.patient_id is None
        if payload.patient_id is None:
            patient = Patient(
                nom=nom,
                prenom=prenom,
                date_naissance=payload.date_naissance,
                created_by=user.username,
            )
            session.add(patient)
            session.flush()
            if patient.id is None:
                raise RuntimeError("L'identifiant du patient n'a pas été généré.")
        else:
            patient = session.get(Patient, payload.patient_id)
            if not patient:
                raise HTTPException(status_code=404, detail="Patient introuvable.")

        item = session.get(StockItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Article introuvable.")
        item_before = audit_snapshot(item)
        qr_units_before = {
            unit.id: audit_snapshot(unit)
            for unit in session.exec(
                select(StockQrUnit).where(StockQrUnit.stock_item_id == item_id)
            ).all()
        }
        qr_unit = session.get(StockQrUnit, payload.qr_unit_id) if payload.qr_unit_id else None
        if payload.qr_unit_id is not None:
            if (
                not qr_unit
                or qr_unit.stock_item_id != item_id
                or qr_unit.numero_serie.strip() != code
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Le QR scanné ne correspond plus à cette sous-entité de stock. Scannez à nouveau le produit.",
                )
            serial = qr_unit.numero_serie
        else:
            identifiers = {item.code_barres, item.numero_serie}
            if code not in identifiers:
                raise HTTPException(
                    status_code=409,
                    detail="Le code scanné ne correspond plus au produit sélectionné. Scannez à nouveau le produit.",
                )
            serial = item.numero_serie

        decrement_stock(session, item, payload.quantity, payload.qr_unit_id)
        withdrawal = StockWithdrawal(
            stock_item_id=item.id,
            patient_id=patient.id,
            denomination=item.denomination,
            quantity=payload.quantity,
            withdrawn_by=user.username,
        )
        session.add(withdrawal)
        session.flush()
        if withdrawal.id is None:
            raise RuntimeError("L'identifiant de la sortie de stock n'a pas été généré.")
        dispensation = PatientDispensation(
            patient_id=patient.id,
            stock_item_id=item.id,
            withdrawal_id=withdrawal.id,
            denomination=item.denomination,
            quantity=payload.quantity,
            scanned_code=code,
            numero_serie=serial,
            expiration_date=(
                qr_unit.expiration_date if qr_unit else item.expiration_date
            ),
            medecin=medecin,
            salle=salle,
            dispensed_by=user.username,
        )
        session.add(dispensation)
        session.flush()
        if withdrawal.id is None or dispensation.id is None or patient.id is None:
            raise RuntimeError("L'identifiant de la délivrance n'a pas été généré.")
        if patient_created:
            record_audit_event(
                session,
                "patient",
                patient.id,
                "création",
                user.username,
                None,
                audit_snapshot(patient),
            )
        record_audit_event(
            session,
            "article de stock",
            item.id,
            "modification",
            user.username,
            item_before,
            audit_snapshot(item),
        )
        for unit_id, before_data in qr_units_before.items():
            unit = session.get(StockQrUnit, unit_id)
            if unit and before_data != audit_snapshot(unit):
                record_audit_event(
                    session,
                    "sous-entité QR",
                    unit_id,
                    "modification",
                    user.username,
                    before_data,
                    audit_snapshot(unit),
                )
        record_audit_event(
            session,
            "sortie de stock",
            withdrawal.id,
            "création",
            user.username,
            None,
            audit_snapshot(withdrawal),
        )
        record_audit_event(
            session,
            "délivrance patient",
            dispensation.id,
            "création",
            user.username,
            None,
            audit_snapshot(dispensation),
        )
        session.commit()
        session.refresh(dispensation)
        return patient_dispensation_read(session, dispensation)


@app.get(
    "/api/patient-dispensations",
    response_model=list[PatientDispensationRead],
)
def list_all_patient_dispensations(
    q: Optional[str] = None,
    patient_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    authorization: Optional[str] = Header(default=None),
):
    current_user(authorization)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="La date de début doit être antérieure ou égale à la date de fin.",
        )

    with Session(engine) as session:
        statement = (
            select(PatientDispensation, Patient, StockItem)
            .join(Patient, Patient.id == PatientDispensation.patient_id)
            .join(StockItem, StockItem.id == PatientDispensation.stock_item_id)
        )
        if patient_id is not None:
            statement = statement.where(
                PatientDispensation.patient_id == patient_id
            )
        search = (q or "").strip().lower()
        if search:
            statement = statement.where(
                or_(
                    func.lower(Patient.nom).contains(search, autoescape=True),
                    func.lower(Patient.prenom).contains(search, autoescape=True),
                    func.lower(Patient.prenom + " " + Patient.nom).contains(
                        search, autoescape=True
                    ),
                    func.lower(Patient.nom + " " + Patient.prenom).contains(
                        search, autoescape=True
                    ),
                    func.lower(PatientDispensation.denomination).contains(
                        search, autoescape=True
                    ),
                    func.lower(PatientDispensation.scanned_code).contains(
                        search, autoescape=True
                    ),
                    func.lower(PatientDispensation.numero_serie).contains(
                        search, autoescape=True
                    ),
                    func.lower(PatientDispensation.dispensed_by).contains(
                        search, autoescape=True
                    ),
                    func.lower(PatientDispensation.medecin).contains(
                        search, autoescape=True
                    ),
                    func.lower(PatientDispensation.salle).contains(
                        search, autoescape=True
                    ),
                )
            )
        if date_from is not None:
            statement = statement.where(
                func.date(PatientDispensation.dispensed_at) >= date_from.isoformat()
            )
        if date_to is not None:
            statement = statement.where(
                func.date(PatientDispensation.dispensed_at) <= date_to.isoformat()
            )
        entries = session.exec(
            statement.order_by(
                PatientDispensation.dispensed_at.desc(),
                PatientDispensation.id.desc(),
            ).limit(500)
        ).all()
        return [
            PatientDispensationRead(
                id=dispensation.id,
                patient_id=patient.id,
                stock_item_id=item.id,
                withdrawal_id=dispensation.withdrawal_id,
                patient_name=f"{patient.prenom} {patient.nom}",
                patient_date_naissance=patient.date_naissance,
                denomination=dispensation.denomination,
                quantity=dispensation.quantity,
                scanned_code=dispensation.scanned_code,
                numero_serie=dispensation.numero_serie,
                expiration_date=dispensation.expiration_date,
                medecin=dispensation.medecin,
                salle=dispensation.salle,
                dispensed_by=dispensation.dispensed_by,
                dispensed_at=dispensation.dispensed_at,
                stock_remaining=item.quantity,
            )
            for dispensation, patient, item in entries
        ]


@app.get(
    "/api/patients/{patient_id}/dispensations",
    response_model=list[PatientDispensationRead],
)
def list_patient_dispensations(
    patient_id: int,
    authorization: Optional[str] = Header(default=None),
):
    current_user(authorization)
    with Session(engine) as session:
        if not session.get(Patient, patient_id):
            raise HTTPException(status_code=404, detail="Patient introuvable.")
        entries = session.exec(
            select(PatientDispensation)
            .where(PatientDispensation.patient_id == patient_id)
            .order_by(
                PatientDispensation.dispensed_at.desc(),
                PatientDispensation.id.desc(),
            )
        ).all()
        return [patient_dispensation_read(session, entry) for entry in entries]


@app.get("/api/stock/qr-units", response_model=list[StockQrUnitRead])
def list_stock_qr_units(authorization: Optional[str] = Header(default=None)):
    current_user(authorization)
    with Session(engine) as session:
        entries = session.exec(
            select(StockQrUnit, StockItem.denomination)
            .join(StockItem, StockItem.id == StockQrUnit.stock_item_id)
            .order_by(StockQrUnit.created_at.desc(), StockQrUnit.id.desc())
        ).all()
        return [
            StockQrUnitRead(
                id=unit.id,
                stock_item_id=unit.stock_item_id,
                denomination=denomination,
                numero_serie=unit.numero_serie,
                quantity=unit.quantity,
                entry_date=unit.entry_date,
                numero_lot=unit.numero_lot,
                expiration_date=unit.expiration_date,
                commentaire=unit.commentaire,
                created_by=unit.created_by,
                created_at=unit.created_at,
            )
            for unit, denomination in entries
        ]


@app.patch("/api/stock/qr-units/{unit_id}", response_model=StockQrUnitRead)
def update_stock_qr_unit(
    unit_id: int,
    payload: StockQrUnitUpdate,
    authorization: Optional[str] = Header(default=None),
):
    actor = require_admin(authorization)
    changes = payload.model_dump(exclude_unset=True)
    with Session(engine) as session:
        unit = session.get(StockQrUnit, unit_id)
        if not unit:
            raise HTTPException(status_code=404, detail="Sous-entité QR introuvable.")
        item = session.get(StockItem, unit.stock_item_id)
        if not item:
            raise RuntimeError("La sous-entité QR ne référence plus un article existant.")
        _ensure_not_locked(session, serial=unit.numero_serie)
        before = audit_snapshot(unit)
        if "numero_serie" in changes and (changes["numero_serie"] or "").strip() != unit.numero_serie.strip():
            serial = (changes["numero_serie"] or "").strip()
            if not serial:
                raise HTTPException(status_code=422, detail="Le numéro de série est requis.")
            duplicate = session.exec(
                select(StockQrUnit.id).where(
                    func.trim(StockQrUnit.numero_serie) == serial,
                    StockQrUnit.id != unit.id,
                )
            ).first()
            if duplicate is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Ce numéro de série est déjà utilisé par une autre sous-entité QR.",
                )
            ensure_unique_stock_serial(session, serial, item.id)
            unit.numero_serie = serial
        if "entry_date" in changes and changes["entry_date"] is not None:
            unit.entry_date = changes["entry_date"]
        if "numero_lot" in changes:
            unit.numero_lot = (changes["numero_lot"] or "").strip() or None
        if "expiration_date" in changes:
            unit.expiration_date = changes["expiration_date"]
        if "commentaire" in changes:
            unit.commentaire = (changes["commentaire"] or "").strip() or None
        session.add(unit)
        record_audit_event(
            session,
            "sous-entité QR",
            unit.id,
            "modification",
            actor.username,
            before,
            audit_snapshot(unit),
        )
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Ce numéro de série est déjà utilisé.",
            ) from error
        session.refresh(unit)
        return StockQrUnitRead(
            id=unit.id,
            stock_item_id=unit.stock_item_id,
            denomination=item.denomination,
            numero_serie=unit.numero_serie,
            quantity=unit.quantity,
            entry_date=unit.entry_date,
            numero_lot=unit.numero_lot,
            expiration_date=unit.expiration_date,
            commentaire=unit.commentaire,
            created_by=unit.created_by,
            created_at=unit.created_at,
        )


@app.delete("/api/stock/qr-units/{unit_id}")
def delete_stock_qr_unit(
    unit_id: int,
    authorization: Optional[str] = Header(default=None),
):
    actor = require_admin(authorization)
    with Session(engine) as session:
        unit = session.get(StockQrUnit, unit_id)
        if not unit:
            raise HTTPException(status_code=404, detail="Sous-entité QR introuvable.")
        item = session.get(StockItem, unit.stock_item_id)
        if not item:
            raise RuntimeError("La sous-entité QR ne référence plus un article existant.")
        _ensure_not_locked(session, serial=unit.numero_serie)
        item_before = audit_snapshot(item)
        unit_before = audit_snapshot(unit)
        result = session.exec(
            update(StockItem)
            .where(
                StockItem.id == unit.stock_item_id,
                StockItem.quantity >= unit.quantity,
            )
            .values(quantity=StockItem.quantity - unit.quantity)
        )
        if result.rowcount != 1:
            raise HTTPException(
                status_code=409,
                detail="La quantité agrégée ne correspond plus à la sous-entité. Actualisez le stock et réessayez.",
            )
        session.refresh(item)
        record_audit_event(
            session,
            "article de stock",
            item.id,
            "modification",
            actor.username,
            item_before,
            audit_snapshot(item),
        )
        record_audit_event(
            session,
            "sous-entité QR",
            unit.id,
            "suppression",
            actor.username,
            unit_before,
            None,
        )
        session.delete(unit)
        session.commit()
    return {"success": True}


@app.post("/api/stock/qr-addition", response_model=StockQrAdditionRead, status_code=201)
def add_stock_by_qr(
    payload: StockQrAdditionRequest,
    authorization: Optional[str] = Header(default=None),
):
    user = current_user(authorization)
    serial = payload.numero_serie.strip()
    if not serial:
        raise HTTPException(status_code=422, detail="Le numéro de série est requis.")
    with Session(engine) as session:
        item = session.get(StockItem, payload.stock_item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Article sélectionné introuvable.")
        existing_unit = session.exec(
            select(StockQrUnit.id).where(
                func.trim(StockQrUnit.numero_serie) == serial
            )
        ).first()
        if existing_unit is not None:
            raise HTTPException(
                status_code=409,
                detail="Cette sous-entité QR existe déjà. Un QR ne peut être ajouté qu'une seule fois.",
            )
        ensure_unique_stock_serial(session, serial, item.id)
        item_before = audit_snapshot(item)
        result = session.exec(
            update(StockItem)
            .where(StockItem.id == item.id)
            .values(quantity=StockItem.quantity + 1)
        )
        if result.rowcount != 1:
            raise HTTPException(status_code=409, detail="L'article a été modifié simultanément. Réessayez.")
        session.refresh(item)
        qr_unit = StockQrUnit(
            stock_item_id=item.id,
            numero_serie=serial,
            quantity=1,
            entry_date=item.entry_date,
            numero_lot=item.numero_lot,
            expiration_date=payload.expiration_date or item.expiration_date,
            created_by=user.username,
        )
        addition = StockQrAddition(
            stock_item_id=item.id,
            denomination=item.denomination,
            numero_serie=serial,
            quantity_after=item.quantity,
            added_by=user.username,
        )
        session.add(qr_unit)
        session.add(addition)
        session.flush()
        if qr_unit.id is None or item.id is None:
            raise RuntimeError("L'identifiant de la sous-entité QR n'a pas été généré.")
        record_audit_event(
            session,
            "article de stock",
            item.id,
            "modification",
            user.username,
            item_before,
            audit_snapshot(item),
        )
        record_audit_event(
            session,
            "sous-entité QR",
            qr_unit.id,
            "création",
            user.username,
            None,
            audit_snapshot(qr_unit),
        )
        record_audit_event(
            session,
            "entrée stock QR",
            addition.id,
            "création",
            user.username,
            None,
            audit_snapshot(addition),
        )
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Cette sous-entité QR existe déjà. Un QR ne peut être ajouté qu'une seule fois.",
            ) from error
        session.refresh(qr_unit)
        session.refresh(addition)
        return StockQrAdditionRead(
            id=addition.id,
            stock_item_id=item.id,
            denomination=item.denomination,
            numero_serie=serial,
            quantity_added=1,
            quantity_after=item.quantity,
            added_by=addition.added_by,
            added_at=addition.added_at,
        )


@app.get("/api/stock/qr-additions", response_model=list[StockQrAdditionRead])
def list_stock_qr_additions(
    authorization: Optional[str] = Header(default=None),
):
    require_admin(authorization)
    with Session(engine) as session:
        entries = session.exec(
            select(StockQrAddition).order_by(
                StockQrAddition.added_at.desc(),
                StockQrAddition.id.desc(),
            )
        ).all()
        return [
            StockQrAdditionRead(
                id=entry.id,
                stock_item_id=entry.stock_item_id,
                denomination=entry.denomination,
                numero_serie=entry.numero_serie,
                quantity_added=1,
                quantity_after=entry.quantity_after,
                added_by=entry.added_by,
                added_at=entry.added_at,
            )
            for entry in entries
        ]


@app.get("/api/stock/withdrawals", response_model=list[StockWithdrawalRead])
def list_stock_withdrawals(authorization: Optional[str] = Header(default=None)):
    require_admin(authorization)
    with Session(engine) as session:
        statement = (
            select(StockWithdrawal, Patient.prenom, Patient.nom)
            .outerjoin(Patient, Patient.id == StockWithdrawal.patient_id)
            .order_by(
            StockWithdrawal.withdrawn_at.desc(),
            StockWithdrawal.id.desc(),
            )
        )
        entries = session.exec(statement).all()
        return [
            StockWithdrawalRead(
                id=withdrawal.id,
                stock_item_id=withdrawal.stock_item_id,
                patient_id=withdrawal.patient_id,
                patient_name=(
                    f"{prenom} {nom}" if prenom is not None and nom is not None else None
                ),
                denomination=withdrawal.denomination,
                quantity=withdrawal.quantity,
                withdrawn_by=withdrawal.withdrawn_by,
                withdrawn_at=withdrawal.withdrawn_at,
            )
            for withdrawal, prenom, nom in entries
        ]

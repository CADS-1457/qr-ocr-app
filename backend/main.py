import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import Column, LargeBinary, delete, func, inspect, or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, create_engine, select

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
    auth_version: int = Field(default=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AppSecret(SQLModel, table=True):
    name: str = Field(primary_key=True, max_length=100)
    value: str


class UserCreate(SQLModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=12, max_length=1024)
    role: Literal["admin", "user"] = "user"


class UserUpdate(SQLModel):
    username: Optional[str] = Field(default=None, min_length=1, max_length=255)
    password: Optional[str] = Field(default=None, min_length=12, max_length=1024)
    role: Optional[Literal["admin", "user"]] = None
    active: Optional[bool] = None


class UserRead(SQLModel):
    id: int
    username: str
    role: str
    active: bool
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
    created_at: datetime


class PatientUpdate(SQLModel):
    nom: Optional[str] = Field(default=None, min_length=1, max_length=255)
    prenom: Optional[str] = Field(default=None, min_length=1, max_length=255)
    date_naissance: Optional[date] = None


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


DATABASE_URL = "sqlite:///./scans.db"
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
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
    with engine.begin() as connection:
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


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
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=422, detail="Le nom d'utilisateur est requis.")
    with Session(engine) as session:
        existing = session.exec(
            select(UserAccount.id).where(UserAccount.username == username)
        ).first()
        if existing is not None:
            raise HTTPException(status_code=409, detail="Ce nom d'utilisateur existe déjà.")
        user = UserAccount(
            username=username,
            password_hash=hash_password(payload.password),
            role=payload.role,
            active=True,
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
    with Session(engine) as session:
        patient = session.get(Patient, patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient introuvable.")
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

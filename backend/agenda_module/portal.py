"""Portail de prise de rendez-vous en ligne.

Routes publiques (/api/portal) : connexion par e-mail + code à usage unique, consultation des
créneaux ouverts, demande de rendez-vous (statut « À confirmer ») et annulation par le patient.
Routes d'administration (/api/agenda/online) : réglages, règles d'ouverture et demandes à confirmer.
"""
import hashlib
import hmac
import logging
import os
import secrets
import smtplib
import ssl
import threading
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .dependencies import get_current_user_id, get_db, require_admin
from .router import AGENDA_TIMEZONE, _audit, _snapshot, ensure_room_capacity, exceeds_parallel

logger = logging.getLogger("portal")

TZ = ZoneInfo(AGENDA_TIMEZONE)
SESSION_COOKIE = "portal_session"
SESSION_HOURS = 2
OTP_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_MAX_PER_EMAIL_HOUR = 5
OTP_MAX_PER_IP_HOUR = 20
INACTIVE_STATES = ("CANCELLED", "NO_SHOW", "POSTPONED")
MAX_WINDOW_DAYS = 14


def ensure_portal_schema(engine) -> None:
    with engine.begin() as connection:
        for statement in (
            "CREATE TABLE IF NOT EXISTS online_booking_settings ("
            "id integer PRIMARY KEY CHECK (id=1), enabled boolean NOT NULL DEFAULT false,"
            "min_notice_hours integer NOT NULL DEFAULT 24, max_days_ahead integer NOT NULL DEFAULT 60,"
            "cancel_notice_hours integer NOT NULL DEFAULT 24, max_pending integer NOT NULL DEFAULT 3,"
            "intro_text text)",
            "INSERT INTO online_booking_settings(id) VALUES(1) ON CONFLICT DO NOTHING",
            "CREATE TABLE IF NOT EXISTS online_booking_rules ("
            "id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
            "room_id uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,"
            "resource_id uuid REFERENCES people_resources(id) ON DELETE CASCADE,"
            "block_id uuid REFERENCES scheduling_blocks(id) ON DELETE CASCADE,"
            "appointment_type_ids uuid[], min_notice_hours integer, max_days_ahead integer,"
            "label text, active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now())",
            "CREATE TABLE IF NOT EXISTS portal_otp (id bigserial PRIMARY KEY, email text NOT NULL,"
            "code_hash text NOT NULL, ip text, attempts integer NOT NULL DEFAULT 0,"
            "used boolean NOT NULL DEFAULT false, expires_at timestamptz NOT NULL,"
            "created_at timestamptz NOT NULL DEFAULT now())",
            "CREATE INDEX IF NOT EXISTS ix_portal_otp_email ON portal_otp(email, created_at)",
            "CREATE TABLE IF NOT EXISTS portal_sessions (token_hash text PRIMARY KEY, email text NOT NULL,"
            "expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now())",
            "CREATE TABLE IF NOT EXISTS portal_patients (email text NOT NULL,"
            "patient_id integer NOT NULL REFERENCES patient(id) ON DELETE CASCADE,"
            "PRIMARY KEY(email, patient_id))",
            "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS online_booking boolean NOT NULL DEFAULT false",
            "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS portal_email text",
        ):
            connection.exec_driver_sql(statement)
    # Une valeur d'enum doit être validée avant d'être utilisable : connexion autocommit dédiée.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("ALTER TYPE appointment_state ADD VALUE IF NOT EXISTS 'TO_CONFIRM'")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO appointment_statuses(code,label,color,edge,builtin,active,sort_order) "
            "VALUES('TO_CONFIRM','À confirmer','#0EA5E9',false,true,true,0) ON CONFLICT (code) DO NOTHING"
        )


# ---------------------------------------------------------------- e-mails

def _smtp_send(to: str, subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        logger.warning("SMTP non configuré : e-mail « %s » non envoyé.", subject)
        if os.getenv("PORTAL_DEV_LOG_MAIL") == "1":
            logger.warning("[DEV] À %s : %s\n%s", to, subject, body)
        return
    message = EmailMessage()
    message["To"] = to
    message["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "no-reply@localhost"
    message["Subject"] = subject
    message.set_content(body)
    security = os.getenv("SMTP_SECURITY", "starttls").lower()
    port = int(os.getenv("SMTP_PORT") or (465 if security == "ssl" else 587))
    try:
        if security == "ssl":
            client = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(host, port, timeout=20)
        with client:
            if security == "starttls":
                client.starttls(context=ssl.create_default_context())
            if os.getenv("SMTP_USER"):
                client.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
            client.send_message(message)
    except Exception:
        logger.exception("Échec d'envoi de l'e-mail « %s ».", subject)


def send_mail_async(to: str, subject: str, body: str) -> None:
    threading.Thread(target=_smtp_send, args=(to, subject, body), daemon=True).start()


SIGNATURE = "\n\nCe message est envoyé automatiquement, merci de ne pas y répondre."


def _fmt(value: datetime) -> str:
    local = value.astimezone(TZ)
    days = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    return f"{days[local.weekday()]} {local:%d/%m/%Y} à {local:%H:%M}"


def notify_status_change(appointment_id: UUID, old_state: str | None, new_state: str) -> None:
    """E-mail au patient quand le secrétariat traite une demande en ligne (jamais bloquant)."""
    try:
        from main import engine

        with Session(engine) as db:
            row = db.execute(
                text(
                    "SELECT a.portal_email,a.starts_at,p.prenom,t.name AS type,r.name AS room "
                    "FROM appointments a JOIN patient p ON p.id=a.patient_id "
                    "LEFT JOIN appointment_types t ON t.id=a.appointment_type_id "
                    "JOIN rooms r ON r.id=a.room_id WHERE a.id=:id AND a.online_booking"
                ),
                {"id": appointment_id},
            ).mappings().first()
        if not row or not row["portal_email"] or old_state != "TO_CONFIRM":
            return
        when = _fmt(row["starts_at"])
        what = f"{row['type'] or 'Rendez-vous'} ({row['room']})"
        if new_state == "PLANNED":
            send_mail_async(
                row["portal_email"], "Votre rendez-vous est confirmé",
                f"Bonjour {row['prenom']},\n\nVotre rendez-vous est confirmé : {what}, le {when}." + SIGNATURE,
            )
        elif new_state in INACTIVE_STATES:
            send_mail_async(
                row["portal_email"], "Votre demande de rendez-vous n'a pas pu être acceptée",
                f"Bonjour {row['prenom']},\n\nNous ne pouvons pas donner suite à votre demande ({what}, {when}). "
                "Vous pouvez choisir un autre créneau sur le portail ou nous contacter." + SIGNATURE,
            )
    except Exception:
        logger.exception("Notification du patient impossible.")


# ---------------------------------------------------------------- outils

def _secret() -> bytes:
    key = os.getenv("ADMIN_SECRET_KEY", "")
    if len(key) < 32:
        raise HTTPException(status_code=503, detail="Service indisponible.")
    return key.encode()


def _digest(value: str) -> str:
    return hmac.new(_secret(), value.encode(), hashlib.sha256).hexdigest()


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _client_ip(request: Request) -> str:
    return (request.headers.get("x-real-ip") or (request.client.host if request.client else "")).strip()[:64]


def _settings(db: Session) -> dict:
    return dict(db.execute(text("SELECT * FROM online_booking_settings WHERE id=1")).mappings().one())


def _require_enabled(db: Session) -> dict:
    settings = _settings(db)
    if not settings["enabled"]:
        raise HTTPException(status_code=503, detail="La réservation en ligne est momentanément indisponible.")
    return settings


def _clean_email(value: str) -> str:
    from main import PATIENT_EMAIL_PATTERN

    email = value.strip().lower()
    if len(email) > 254 or not PATIENT_EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=422, detail="Adresse e-mail invalide.")
    return email


def portal_email(request: Request, db: Session = Depends(get_db)) -> str:
    token = request.cookies.get(SESSION_COOKIE)
    email = None
    if token:
        email = db.execute(
            text("SELECT email FROM portal_sessions WHERE token_hash=:h AND expires_at>now()"),
            {"h": _digest(token)},
        ).scalar_one_or_none()
    if not email:
        raise HTTPException(status_code=401, detail="Session expirée, veuillez vous reconnecter.")
    return email


# ---------------------------------------------------------------- créneaux

def open_slots(db: Session, start: date, end: date, type_id: UUID, settings: dict,
               room_id: UUID | None = None, doctor_id: UUID | None = None) -> list[dict]:
    """Créneaux réservables en ligne sur [start, end[ pour un type de rendez-vous."""
    kind = db.execute(
        text("SELECT id,name,duration_minutes,doctor_required FROM appointment_types WHERE id=:id AND active"),
        {"id": type_id},
    ).mappings().first()
    if not kind:
        raise HTTPException(status_code=404, detail="Type de rendez-vous introuvable.")
    rules = db.execute(
        text(
            "SELECT * FROM online_booking_rules WHERE active "
            "AND (appointment_type_ids IS NULL OR CAST(:t AS uuid)=ANY(appointment_type_ids))"
        ),
        {"t": type_id},
    ).mappings().all()
    if not rules:
        return []
    now = datetime.now(TZ)
    candidates: dict[tuple, dict] = {}
    for rule in rules:
        notice = rule["min_notice_hours"] if rule["min_notice_hours"] is not None else settings["min_notice_hours"]
        horizon = rule["max_days_ahead"] if rule["max_days_ahead"] is not None else settings["max_days_ahead"]
        earliest = now + timedelta(hours=notice)
        last_day = now.date() + timedelta(days=horizon)
        blocks = db.execute(
            text(
                "SELECT b.*,r.name AS room,r.max_parallel,p.display_name AS doctor "
                "FROM scheduling_blocks b JOIN rooms r ON r.id=b.room_id AND r.active "
                "LEFT JOIN people_resources p ON p.id=COALESCE(b.resource_id,CAST(:rd AS uuid)) "
                "WHERE b.active AND b.room_id=:room "
                "AND (CAST(:block AS uuid) IS NULL OR b.id=CAST(:block AS uuid)) "
                "AND (CAST(:rd AS uuid) IS NULL OR b.resource_id IS NULL OR b.resource_id=CAST(:rd AS uuid))"
            ),
            {"room": rule["room_id"], "block": rule["block_id"], "rd": rule["resource_id"]},
        ).mappings().all()
        for block in blocks:
            doctor = block["resource_id"] or rule["resource_id"]
            if kind["doctor_required"] and not doctor:
                continue
            if room_id and block["room_id"] != room_id or doctor_id and doctor != doctor_id:
                continue
            length = kind["duration_minutes"] or block["slot_minutes"]
            day = start
            while day < end and day <= last_day:
                valid = (
                    block["weekday"] == day.weekday()
                    and (block["valid_from"] is None or block["valid_from"] <= day)
                    and (block["valid_to"] is None or block["valid_to"] >= day)
                )
                if valid:
                    cursor = datetime.combine(day, block["start_time"], tzinfo=TZ)
                    limit = datetime.combine(day, block["end_time"], tzinfo=TZ)
                    while cursor + timedelta(minutes=length) <= limit:
                        if cursor >= earliest:
                            candidates[(block["room_id"], doctor, cursor)] = {
                                "starts_at": cursor, "ends_at": cursor + timedelta(minutes=length),
                                "room_id": block["room_id"], "room": block["room"],
                                "doctor_id": doctor, "doctor": block["doctor"],
                                "max_parallel": block["max_parallel"],
                            }
                        cursor += timedelta(minutes=block["slot_minutes"])
                day += timedelta(days=1)
    if not candidates:
        return []
    window_start = datetime.combine(start, time.min, tzinfo=TZ)
    window_end = datetime.combine(end, time.min, tzinfo=TZ)
    closures = db.execute(
        text(
            "SELECT e.room_id,e.resource_id,e.start_date,e.end_date,e.start_time,e.end_time "
            "FROM schedule_exceptions e JOIN schedule_exception_types t ON t.id=e.type_id "
            "WHERE t.blocks_booking AND e.end_date>=:s AND e.start_date<=:e"
        ),
        {"s": start, "e": end},
    ).mappings().all()
    busy: dict[UUID, list[tuple[datetime, datetime]]] = {}
    for row in db.execute(
        text(
            "SELECT room_id,starts_at,ends_at FROM appointments WHERE starts_at<:e AND ends_at>:s "
            "AND state NOT IN ('CANCELLED','NO_SHOW','POSTPONED')"
        ),
        {"s": window_start, "e": window_end},
    ).mappings():
        busy.setdefault(row["room_id"], []).append((row["starts_at"], row["ends_at"]))

    def closed(slot: dict) -> bool:
        day = slot["starts_at"].date()
        for item in closures:
            if item["room_id"] and item["room_id"] != slot["room_id"]:
                continue
            if item["resource_id"] and item["resource_id"] != slot["doctor_id"]:
                continue
            if not item["start_date"] <= day <= item["end_date"]:
                continue
            exception_end = item["end_time"] or time(23, 59, 59, 999999)
            exception_start = item["start_time"] or time.min
            if slot["starts_at"].time() < exception_end and (
                slot["ends_at"].date() > day or slot["ends_at"].time() > exception_start
            ):
                return True
        return False

    result = []
    for slot in sorted(candidates.values(), key=lambda s: (s["starts_at"], s["room"])):
        if closed(slot) or exceeds_parallel(
            busy.get(slot["room_id"], []), slot["starts_at"], slot["ends_at"], slot["max_parallel"]
        ):
            continue
        result.append(slot)
    return result


def _public_slot(slot: dict) -> dict:
    return {
        "starts_at": slot["starts_at"].isoformat(), "ends_at": slot["ends_at"].isoformat(),
        "date": slot["starts_at"].date().isoformat(), "time": f"{slot['starts_at']:%H:%M}",
        "room_id": str(slot["room_id"]), "room": slot["room"],
        "doctor_id": str(slot["doctor_id"]) if slot["doctor_id"] else None, "doctor": slot["doctor"],
    }


# ---------------------------------------------------------------- portail public

portal = APIRouter(prefix="/api/portal", tags=["portail"], dependencies=[Depends(_no_store)])


class CodeRequest(BaseModel):
    email: str = Field(max_length=254)


class CodeCheck(BaseModel):
    email: str = Field(max_length=254)
    code: str = Field(pattern=r"^\d{6}$")


class PortalPatient(BaseModel):
    nom: str = Field(min_length=1, max_length=100)
    prenom: str = Field(min_length=1, max_length=100)
    date_naissance: date
    mobile: str | None = Field(default=None, max_length=30)


class PortalBooking(BaseModel):
    type_id: UUID
    room_id: UUID
    doctor_id: UUID | None = None
    starts_at: datetime
    patient: PortalPatient
    notes: str | None = Field(default=None, max_length=500)
    consent: bool


@portal.get("/config")
def portal_config(db: Session = Depends(get_db)) -> dict[str, object]:
    settings = _settings(db)
    return {
        "enabled": settings["enabled"], "intro_text": settings["intro_text"],
        "cancel_notice_hours": settings["cancel_notice_hours"],
    }


@portal.post("/auth/request-code")
def request_code(
    payload: CodeRequest, request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db)
) -> dict[str, bool]:
    _require_enabled(db)
    email = _clean_email(payload.email)
    ip = _client_ip(request)
    per_email = db.execute(
        text("SELECT count(*) FROM portal_otp WHERE email=:e AND created_at>now()-interval '1 hour'"),
        {"e": email},
    ).scalar_one()
    per_ip = db.execute(
        text("SELECT count(*) FROM portal_otp WHERE ip=:i AND created_at>now()-interval '1 hour'"),
        {"i": ip},
    ).scalar_one()
    if per_email >= OTP_MAX_PER_EMAIL_HOUR or per_ip >= OTP_MAX_PER_IP_HOUR:
        raise HTTPException(status_code=429, detail="Trop de demandes. Réessayez dans une heure.")
    code = f"{secrets.randbelow(10**6):06d}"
    db.execute(
        text(
            "INSERT INTO portal_otp(email,code_hash,ip,expires_at) "
            "VALUES(:e,:h,:i,now()+make_interval(mins=>:m))"
        ),
        {"e": email, "h": _digest(f"{email}:{code}"), "i": ip, "m": OTP_MINUTES},
    )
    db.execute(text("DELETE FROM portal_otp WHERE created_at<now()-interval '2 days'"))
    db.execute(text("DELETE FROM portal_sessions WHERE expires_at<now()"))
    db.commit()
    tasks.add_task(
        _smtp_send, email, "Votre code de connexion",
        f"Votre code de connexion est : {code}\n\nIl est valable {OTP_MINUTES} minutes. "
        "Si vous n'êtes pas à l'origine de cette demande, ignorez ce message." + SIGNATURE,
    )
    return {"ok": True}


@portal.post("/auth/verify")
def verify_code(
    payload: CodeCheck, response: Response, db: Session = Depends(get_db)
) -> dict[str, str]:
    _require_enabled(db)
    email = _clean_email(payload.email)
    otp = db.execute(
        text(
            "SELECT id,code_hash,attempts FROM portal_otp WHERE email=:e AND NOT used "
            "AND expires_at>now() ORDER BY id DESC LIMIT 1 FOR UPDATE"
        ),
        {"e": email},
    ).mappings().first()
    invalid = HTTPException(status_code=400, detail="Code invalide ou expiré.")
    if not otp or otp["attempts"] >= OTP_MAX_ATTEMPTS:
        raise invalid
    ok = hmac.compare_digest(otp["code_hash"], _digest(f"{email}:{payload.code}"))
    db.execute(
        text("UPDATE portal_otp SET attempts=attempts+1, used=:u WHERE id=:id"),
        {"u": ok, "id": otp["id"]},
    )
    if not ok:
        db.commit()
        raise invalid
    token = secrets.token_urlsafe(32)
    db.execute(
        text("INSERT INTO portal_sessions(token_hash,email,expires_at) VALUES(:h,:e,now()+make_interval(hours=>:n))"),
        {"h": _digest(token), "e": email, "n": SESSION_HOURS},
    )
    db.commit()
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_HOURS * 3600, httponly=True, secure=True,
        samesite="strict", path="/api/portal",
    )
    return {"email": email}


@portal.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, bool]:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute(text("DELETE FROM portal_sessions WHERE token_hash=:h"), {"h": _digest(token)})
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/api/portal")
    return {"ok": True}


@portal.get("/me")
def me(email: str = Depends(portal_email)) -> dict[str, str]:
    return {"email": email}


@portal.get("/offer")
def offer(db: Session = Depends(get_db), _: str = Depends(portal_email)) -> list[dict[str, object]]:
    _require_enabled(db)
    return [
        {"id": str(row["id"]), "name": row["name"], "duration_minutes": row["duration_minutes"]}
        for row in db.execute(
            text(
                "SELECT DISTINCT t.id,t.name,t.duration_minutes FROM appointment_types t "
                "JOIN online_booking_rules r ON r.active "
                "AND (r.appointment_type_ids IS NULL OR t.id=ANY(r.appointment_type_ids)) "
                "WHERE t.active ORDER BY t.name"
            )
        ).mappings()
    ]


@portal.get("/slots")
def slots(
    type_id: UUID,
    start: date,
    end: date,
    doctor_id: UUID | None = None,
    db: Session = Depends(get_db),
    _: str = Depends(portal_email),
) -> list[dict[str, object]]:
    settings = _require_enabled(db)
    if end <= start or (end - start).days > MAX_WINDOW_DAYS:
        raise HTTPException(status_code=422, detail=f"Période invalide ({MAX_WINDOW_DAYS} jours maximum).")
    return [_public_slot(slot) for slot in open_slots(db, start, end, type_id, settings, doctor_id=doctor_id)]


def _resolve_patient(db: Session, email: str, person: PortalPatient) -> int:
    from main import PATIENT_MOBILE_PATTERN

    nom, prenom = person.nom.strip(), person.prenom.strip()
    mobile = (person.mobile or "").strip() or None
    if not nom or not prenom:
        raise HTTPException(status_code=422, detail="Le nom et le prénom sont obligatoires.")
    if not date(1900, 1, 2) <= person.date_naissance <= date.today():
        raise HTTPException(status_code=422, detail="Date de naissance invalide.")
    if mobile and not PATIENT_MOBILE_PATTERN.match(mobile):
        raise HTTPException(status_code=422, detail="Numéro de mobile invalide.")
    params = {"nom": nom, "prenom": prenom, "dob": person.date_naissance, "email": email}
    patient_id = db.execute(
        text(
            "SELECT id FROM patient WHERE NOT is_temporary AND lower(nom)=lower(:nom) "
            "AND lower(prenom)=lower(:prenom) AND date_naissance=:dob AND lower(email)=:email LIMIT 1"
        ),
        params,
    ).scalar_one_or_none() or db.execute(
        text(
            "SELECT p.id FROM patient p JOIN portal_patients pp ON pp.patient_id=p.id AND pp.email=:email "
            "WHERE lower(p.nom)=lower(:nom) AND lower(p.prenom)=lower(:prenom) AND p.date_naissance=:dob LIMIT 1"
        ),
        params,
    ).scalar_one_or_none()
    if patient_id is None:
        patient_id = db.execute(
            text(
                "INSERT INTO patient (nom,prenom,date_naissance,mobile,email,sms_consent,email_consent,"
                "is_temporary,created_by,created_at) "
                "VALUES (:nom,:prenom,:dob,:mobile,:email,false,true,true,'portail-en-ligne',now()) RETURNING id"
            ),
            {**params, "mobile": mobile},
        ).scalar_one()
    db.execute(
        text("INSERT INTO portal_patients(email,patient_id) VALUES(:e,:p) ON CONFLICT DO NOTHING"),
        {"e": email, "p": patient_id},
    )
    return patient_id


@portal.post("/appointments", status_code=201)
def book(
    payload: PortalBooking,
    tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    email: str = Depends(portal_email),
) -> dict[str, str]:
    settings = _require_enabled(db)
    if not payload.consent:
        raise HTTPException(status_code=422, detail="Votre accord sur le traitement des données est requis.")
    if payload.starts_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="Date et heure invalides.")
    starts = payload.starts_at.astimezone(TZ)
    pending = db.execute(
        text(
            "SELECT count(*) FROM appointments WHERE portal_email=:e AND starts_at>now() "
            "AND state IN ('TO_CONFIRM','PLANNED')"
        ),
        {"e": email},
    ).scalar_one()
    if pending >= settings["max_pending"]:
        raise HTTPException(
            status_code=409,
            detail="Vous avez atteint le nombre maximum de rendez-vous à venir. Contactez-nous pour en ajouter.",
        )
    day = starts.date()
    match = next(
        (
            slot for slot in open_slots(
                db, day, day + timedelta(days=1), payload.type_id, settings, payload.room_id, payload.doctor_id
            )
            if slot["starts_at"] == starts and slot["room_id"] == payload.room_id
            and slot["doctor_id"] == payload.doctor_id
        ),
        None,
    )
    if not match:
        raise HTTPException(status_code=409, detail="Ce créneau n'est plus disponible. Choisissez-en un autre.")
    patient_id = _resolve_patient(db, email, payload.patient)
    ensure_room_capacity(db, payload.room_id, patient_id, match["starts_at"], match["ends_at"])
    note = "Demande en ligne" + (f" : {payload.notes.strip()}" if payload.notes and payload.notes.strip() else "")
    appointment_id = db.execute(
        text(
            "INSERT INTO appointments(patient_id,appointment_type_id,room_id,doctor_id,starts_at,ends_at,"
            "phone,notes,current_location_id,state,online_booking,portal_email) "
            "VALUES(:patient,:type,:room,:doctor,:s,:e,:phone,:notes,:room,"
            "CAST('TO_CONFIRM' AS appointment_state),true,:email) RETURNING id"
        ),
        {
            "patient": patient_id, "type": payload.type_id, "room": payload.room_id,
            "doctor": payload.doctor_id, "s": match["starts_at"], "e": match["ends_at"],
            "phone": (payload.patient.mobile or "").strip() or None, "notes": note, "email": email,
        },
    ).scalar_one()
    db.execute(
        text(
            "INSERT INTO appointment_status_history(appointment_id,new_state,comment) "
            "VALUES(:id,'TO_CONFIRM','Demande en ligne')"
        ),
        {"id": appointment_id},
    )
    _audit(db, appointment_id, "CREATE", None, None, _snapshot(db, appointment_id))
    db.commit()
    tasks.add_task(
        _smtp_send, email, "Demande de rendez-vous reçue",
        f"Bonjour {payload.patient.prenom.strip()},\n\nNous avons bien reçu votre demande pour le "
        f"{_fmt(match['starts_at'])} ({match['room']}). Elle sera confirmée par notre secrétariat : "
        "vous recevrez un e-mail de confirmation." + SIGNATURE,
    )
    return {"id": str(appointment_id)}


@portal.get("/appointments")
def my_appointments(
    db: Session = Depends(get_db), email: str = Depends(portal_email)
) -> list[dict[str, object]]:
    settings = _settings(db)
    rows = db.execute(
        text(
            "SELECT a.id,a.starts_at,a.ends_at,a.state,t.name AS type,r.name AS room,d.display_name AS doctor,"
            "p.prenom,p.nom,COALESCE(s.label,a.state::text) AS state_label "
            "FROM appointments a JOIN patient p ON p.id=a.patient_id "
            "JOIN portal_patients pp ON pp.patient_id=a.patient_id AND pp.email=:e "
            "JOIN rooms r ON r.id=a.room_id "
            "LEFT JOIN appointment_types t ON t.id=a.appointment_type_id "
            "LEFT JOIN people_resources d ON d.id=a.doctor_id "
            "LEFT JOIN appointment_statuses s ON s.code=a.state::text "
            "WHERE a.starts_at>now() AND a.state<>'CANCELLED' ORDER BY a.starts_at"
        ),
        {"e": email},
    ).mappings().all()
    limit = datetime.now(TZ) + timedelta(hours=settings["cancel_notice_hours"])
    return [
        {
            "id": str(row["id"]), "starts_at": row["starts_at"].isoformat(), "type": row["type"],
            "room": row["room"], "doctor": row["doctor"], "patient": f"{row['prenom']} {row['nom']}",
            "state": row["state"], "state_label": row["state_label"],
            "can_cancel": row["state"] in ("TO_CONFIRM", "PLANNED") and row["starts_at"] > limit,
        }
        for row in rows
    ]


@portal.post("/appointments/{appointment_id}/cancel")
def cancel_appointment(
    appointment_id: UUID, db: Session = Depends(get_db), email: str = Depends(portal_email)
) -> dict[str, bool]:
    settings = _settings(db)
    row = db.execute(
        text(
            "SELECT a.state,a.starts_at FROM appointments a "
            "JOIN portal_patients pp ON pp.patient_id=a.patient_id AND pp.email=:e "
            "WHERE a.id=:id FOR UPDATE OF a"
        ),
        {"id": appointment_id, "e": email},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Rendez-vous introuvable.")
    if row["state"] not in ("TO_CONFIRM", "PLANNED"):
        raise HTTPException(status_code=409, detail="Ce rendez-vous ne peut plus être annulé en ligne.")
    if row["starts_at"] <= datetime.now(TZ) + timedelta(hours=settings["cancel_notice_hours"]):
        raise HTTPException(
            status_code=409,
            detail=f"L'annulation en ligne n'est possible que {settings['cancel_notice_hours']} h avant le rendez-vous. "
            "Veuillez nous contacter.",
        )
    before = _snapshot(db, appointment_id)
    db.execute(
        text(
            "INSERT INTO appointment_status_history(appointment_id,old_state,new_state,comment) "
            "VALUES(:id,:old,'CANCELLED','Annulé par le patient (portail)')"
        ),
        {"id": appointment_id, "old": row["state"]},
    )
    db.execute(
        text("UPDATE appointments SET state='CANCELLED',updated_at=now(),version=version+1 WHERE id=:id"),
        {"id": appointment_id},
    )
    from .service import release_planned_stock

    release_planned_stock(db, appointment_id)
    _audit(db, appointment_id, "UPDATE", None, before, _snapshot(db, appointment_id))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- administration

admin = APIRouter(prefix="/api/agenda/online", tags=["réservation en ligne"])


class SettingsPayload(BaseModel):
    enabled: bool
    min_notice_hours: int = Field(ge=0, le=24 * 60)
    max_days_ahead: int = Field(ge=1, le=365)
    cancel_notice_hours: int = Field(ge=0, le=24 * 30)
    max_pending: int = Field(ge=1, le=20)
    intro_text: str | None = Field(default=None, max_length=1000)


class RulePayload(BaseModel):
    room_id: UUID
    resource_id: UUID | None = None
    block_id: UUID | None = None
    appointment_type_ids: list[UUID] | None = None
    min_notice_hours: int | None = Field(default=None, ge=0, le=24 * 60)
    max_days_ahead: int | None = Field(default=None, ge=1, le=365)
    label: str | None = Field(default=None, max_length=120)
    active: bool = True


RULE_SELECT = (
    "SELECT r.id,r.room_id,rm.name AS room,r.resource_id,p.display_name AS doctor,r.block_id,"
    "CASE WHEN b.id IS NULL THEN NULL ELSE (ARRAY['Lun','Mar','Mer','Jeu','Ven','Sam','Dim'])[b.weekday+1]"
    "||' '||to_char(b.start_time,'HH24:MI')||'–'||to_char(b.end_time,'HH24:MI') END AS block,"
    "r.appointment_type_ids,r.min_notice_hours,r.max_days_ahead,r.label,r.active "
    "FROM online_booking_rules r JOIN rooms rm ON rm.id=r.room_id "
    "LEFT JOIN people_resources p ON p.id=r.resource_id LEFT JOIN scheduling_blocks b ON b.id=r.block_id"
)


def _check_rule(db: Session, payload: RulePayload) -> None:
    if payload.block_id:
        block = db.execute(
            text("SELECT room_id,resource_id FROM scheduling_blocks WHERE id=:id"), {"id": payload.block_id}
        ).mappings().first()
        if not block or block["room_id"] != payload.room_id:
            raise HTTPException(status_code=422, detail="La plage n'appartient pas à cet agenda.")
        if block["resource_id"] and payload.resource_id and block["resource_id"] != payload.resource_id:
            raise HTTPException(status_code=422, detail="La plage appartient à un autre médecin.")


def _rule_row(row) -> dict:
    item = dict(row)
    for key in ("id", "room_id", "resource_id", "block_id"):
        item[key] = str(item[key]) if item[key] else None
    item["appointment_type_ids"] = (
        [str(value) for value in item["appointment_type_ids"]] if item["appointment_type_ids"] else None
    )
    return item


@admin.get("/settings", dependencies=[Depends(require_admin)])
def get_settings(db: Session = Depends(get_db)) -> dict:
    return _settings(db)


@admin.put("/settings", dependencies=[Depends(require_admin)])
def put_settings(payload: SettingsPayload, db: Session = Depends(get_db)) -> dict:
    db.execute(
        text(
            "UPDATE online_booking_settings SET enabled=:enabled,min_notice_hours=:min_notice_hours,"
            "max_days_ahead=:max_days_ahead,cancel_notice_hours=:cancel_notice_hours,"
            "max_pending=:max_pending,intro_text=:intro_text WHERE id=1"
        ),
        payload.model_dump(),
    )
    db.commit()
    return _settings(db)


@admin.get("/rules", dependencies=[Depends(require_admin)])
def list_rules(db: Session = Depends(get_db)) -> list[dict]:
    return [_rule_row(row) for row in db.execute(text(f"{RULE_SELECT} ORDER BY rm.name,p.display_name,b.weekday,b.start_time")).mappings()]


@admin.post("/rules", status_code=201, dependencies=[Depends(require_admin)])
def create_rule(payload: RulePayload, db: Session = Depends(get_db)) -> dict:
    _check_rule(db, payload)
    rule_id = db.execute(
        text(
            "INSERT INTO online_booking_rules(room_id,resource_id,block_id,appointment_type_ids,"
            "min_notice_hours,max_days_ahead,label,active) VALUES(:room_id,:resource_id,:block_id,"
            "CAST(:types AS uuid[]),:min_notice_hours,:max_days_ahead,:label,:active) RETURNING id"
        ),
        {**payload.model_dump(exclude={"appointment_type_ids"}), "types": payload.appointment_type_ids},
    ).scalar_one()
    db.commit()
    return {"id": str(rule_id)}


@admin.put("/rules/{rule_id}", dependencies=[Depends(require_admin)])
def update_rule(rule_id: UUID, payload: RulePayload, db: Session = Depends(get_db)) -> dict:
    _check_rule(db, payload)
    updated = db.execute(
        text(
            "UPDATE online_booking_rules SET room_id=:room_id,resource_id=:resource_id,block_id=:block_id,"
            "appointment_type_ids=CAST(:types AS uuid[]),min_notice_hours=:min_notice_hours,"
            "max_days_ahead=:max_days_ahead,label=:label,active=:active WHERE id=:id"
        ),
        {**payload.model_dump(exclude={"appointment_type_ids"}), "types": payload.appointment_type_ids, "id": rule_id},
    ).rowcount
    if not updated:
        raise HTTPException(status_code=404, detail="Règle introuvable.")
    db.commit()
    return {"id": str(rule_id)}


@admin.delete("/rules/{rule_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_rule(rule_id: UUID, db: Session = Depends(get_db)) -> None:
    if not db.execute(text("DELETE FROM online_booking_rules WHERE id=:id"), {"id": rule_id}).rowcount:
        raise HTTPException(status_code=404, detail="Règle introuvable.")
    db.commit()


@admin.get("/pending")
def pending_requests(
    db: Session = Depends(get_db), _: int = Depends(get_current_user_id)
) -> list[dict[str, object]]:
    return [
        {
            "id": str(row["id"]), "starts_at": row["starts_at"].isoformat(), "patient": row["patient"],
            "patient_id": row["patient_id"], "email": row["portal_email"], "phone": row["phone"],
            "type": row["type"], "room": row["room"], "doctor": row["doctor"], "notes": row["notes"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in db.execute(
            text(
                "SELECT a.id,a.starts_at,a.patient_id,a.portal_email,a.phone,a.notes,a.created_at,"
                "p.prenom||' '||p.nom AS patient,t.name AS type,r.name AS room,d.display_name AS doctor "
                "FROM appointments a JOIN patient p ON p.id=a.patient_id JOIN rooms r ON r.id=a.room_id "
                "LEFT JOIN appointment_types t ON t.id=a.appointment_type_id "
                "LEFT JOIN people_resources d ON d.id=a.doctor_id "
                "WHERE a.online_booking AND a.state='TO_CONFIRM' ORDER BY a.starts_at"
            )
        ).mappings()
    ]

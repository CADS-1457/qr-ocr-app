import re
import unicodedata
import json
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .dependencies import get_current_user_id, get_db, is_admin_user, require_admin
from .schemas import (
    AppointmentCreate,
    AppointmentTypeCreate,
    ResourceCreate,
    RoomOrder,
    StatusCreate,
    StatusUpdate,
    ResourceUpdate,
    AppointmentTypeUpdate,
    AgendaGroupPayload,
    ExceptionPayload,
    ExceptionTypePayload,
    PatientCreate,
    PatientReconcile,
    PlannedStockAdd,
    RestoreDeletedAppointment,
    RoomCreate,
    RoomUpdate,    SchedulingBlockCreate,
    SchedulingBlockUpdate,
    StockItemCreate,
    StockLocationCreate,
    StockLocationRooms,
    StockUnitCreate,
    StatusRequest,
    TransferRequest,
)
from .service import CLOSED_STOCK_STATES, apply_state_to_planned_stock, release_planned_stock, transfer

router = APIRouter(prefix="/api/agenda", tags=["agenda"])

AGENDA_TIMEZONE = "Europe/Brussels"
BLOCK_COLUMNS = (
    "b.id,b.room_id,r.name AS room,b.resource_id,p.display_name AS resource,"
    "b.weekday,to_char(b.start_time,'HH24:MI') AS start_time,"
    "to_char(b.end_time,'HH24:MI') AS end_time,b.slot_minutes,b.label,b.color,r.color AS room_color,"
    "b.valid_from,b.valid_to,b.active"
)
BLOCK_FROM = (
    "FROM scheduling_blocks b JOIN rooms r ON r.id=b.room_id "
    "LEFT JOIN people_resources p ON p.id=b.resource_id"
)


def _block_overlap_exists(db: Session, room_id: UUID, resource_id: UUID | None, weekday: int,
                          start_time: time, end_time: time, valid_from: date | None,
                          valid_to: date | None, exclude_id: UUID | None = None) -> bool:
    return bool(
        db.execute(
            text(
                "SELECT EXISTS(SELECT 1 FROM scheduling_blocks WHERE active AND room_id=:room_id "
                "AND resource_id IS NOT DISTINCT FROM CAST(:resource_id AS uuid) "
                "AND weekday=:weekday AND start_time<:end_time AND end_time>:start_time "
                "AND (valid_to IS NULL OR CAST(:valid_from AS date) IS NULL OR valid_to>=CAST(:valid_from AS date)) "
                "AND (valid_from IS NULL OR CAST(:valid_to AS date) IS NULL OR valid_from<=CAST(:valid_to AS date)) "
                "AND (CAST(:exclude_id AS uuid) IS NULL OR id<>CAST(:exclude_id AS uuid)))"
            ),
            {
                "room_id": room_id, "resource_id": resource_id, "weekday": weekday,
                "start_time": start_time, "end_time": end_time, "valid_from": valid_from,
                "valid_to": valid_to, "exclude_id": exclude_id,
            },
        ).scalar_one()
    )


def exceeds_parallel(intervals: list[tuple[datetime, datetime]], start: datetime,
                     end: datetime, limit: int) -> bool:
    """Vrai si ajouter [start,end[ dépasse `limit` rendez-vous simultanés à un instant donné."""
    overlapping = [(s, e) for s, e in intervals if s < end and e > start]
    points = [start] + [s for s, _ in overlapping if start < s < end]
    return any(
        sum(1 for s, e in overlapping if s <= point < e) + 1 > limit for point in points
    )


def ensure_room_capacity(db: Session, room_id: UUID, patient_id: int,
                         starts_at: datetime, ends_at: datetime,
                         exclude_id: UUID | None = None) -> None:
    """Applique le plafond de rendez-vous simultanés de la salle (surréservation)."""
    # Verrou transactionnel par salle : évite deux réservations concurrentes au-delà du plafond.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(CAST(:room AS text)))"), {"room": str(room_id)})
    limit = db.execute(
        text("SELECT max_parallel FROM rooms WHERE id=:id"), {"id": room_id}
    ).scalar_one_or_none()
    if limit is None:
        raise HTTPException(status_code=404, detail="Salle introuvable.")
    rows = db.execute(
        text(
            "SELECT patient_id,starts_at,ends_at FROM appointments "
            "WHERE room_id=:room AND starts_at<:e AND ends_at>:s "
            "AND state NOT IN ('CANCELLED','NO_SHOW','POSTPONED') "
            "AND (CAST(:exclude AS uuid) IS NULL OR id<>CAST(:exclude AS uuid))"
        ),
        {"room": room_id, "s": starts_at, "e": ends_at, "exclude": exclude_id},
    ).mappings().all()
    if any(row["patient_id"] == patient_id for row in rows):
        raise HTTPException(
            status_code=409, detail="Ce patient a déjà un rendez-vous dans cette salle sur cette plage."
        )
    if exceeds_parallel([(row["starts_at"], row["ends_at"]) for row in rows], starts_at, ends_at, limit):
        raise HTTPException(
            status_code=409,
            detail=(
                "La salle est occupée sur cette plage."
                if limit == 1
                else f"Capacité atteinte : {limit} rendez-vous simultanés maximum pour cette salle."
            ),
        )


def ensure_slot_allowed(db: Session, room_id: UUID, doctor_id: UUID | None,
                        starts_at: datetime, ends_at: datetime, allow_closed: bool = False) -> None:
    """Refuse un rendez-vous hors des plages définies, si la salle en possède.

    Une fermeture exceptionnelle bloque tout le monde, sauf un administrateur (allow_closed)."""
    closed = None if allow_closed else db.execute(
        text(
            "SELECT t.name,e.note FROM schedule_exceptions e "
            "JOIN schedule_exception_types t ON t.id=e.type_id "
            "WHERE t.blocks_booking AND (e.room_id IS NULL OR e.room_id=:room) "
            "AND (e.resource_id IS NULL OR e.resource_id=CAST(:doctor AS uuid)) "
            "AND (CAST(:s AS timestamptz) AT TIME ZONE :tz)::date BETWEEN e.start_date AND e.end_date "
            "AND (CAST(:s AS timestamptz) AT TIME ZONE :tz)::time < COALESCE(e.end_time, TIME '23:59:59.999999') "
            "AND ((CAST(:e AS timestamptz) AT TIME ZONE :tz)::date > (CAST(:s AS timestamptz) AT TIME ZONE :tz)::date "
            "OR (CAST(:e AS timestamptz) AT TIME ZONE :tz)::time > COALESCE(e.start_time, TIME '00:00')) "
            "LIMIT 1"
        ),
        {"s": starts_at, "e": ends_at, "tz": AGENDA_TIMEZONE, "room": room_id, "doctor": doctor_id},
    ).mappings().first()
    if closed:
        raise HTTPException(
            status_code=422,
            detail=f"Planning indisponible ce jour-là : {closed['name']}"
            + (f" ({closed['note']})" if closed["note"] else "") + ".",
        )
    row = db.execute(
        text(
            "WITH t AS (SELECT (CAST(:s AS timestamptz) AT TIME ZONE :tz) AS ls,"
            "(CAST(:e AS timestamptz) AT TIME ZONE :tz) AS le) "
            "SELECT EXISTS(SELECT 1 FROM scheduling_blocks WHERE room_id=:room AND active) AS restricted,"
            "EXISTS(SELECT 1 FROM scheduling_blocks b, t WHERE b.room_id=:room AND b.active "
            "AND b.weekday=EXTRACT(isodow FROM t.ls)::int-1 AND t.ls::date=t.le::date "
            "AND t.ls::time>=b.start_time AND t.le::time<=b.end_time "
            "AND (b.valid_from IS NULL OR b.valid_from<=t.ls::date) "
            "AND (b.valid_to IS NULL OR b.valid_to>=t.ls::date) "
            "AND (b.resource_id IS NULL OR b.resource_id=CAST(:doctor AS uuid)) "
            "AND MOD((EXTRACT(EPOCH FROM (t.ls::time-b.start_time))/60)::int,b.slot_minutes)=0) AS allowed"
        ),
        {"s": starts_at, "e": ends_at, "tz": AGENDA_TIMEZONE, "room": room_id, "doctor": doctor_id},
    ).mappings().one()
    if row["restricted"] and not row["allowed"]:
        raise HTTPException(
            status_code=422,
            detail=(
                "Ce créneau est hors des plages de rendez-vous définies pour cette salle "
                "(début aligné sur le pas de la plage, fin comprise dans la plage)."
            ),
        )


def get_patient_contact(db: Session, patient_id: int) -> dict[str, object] | None:
    columns = {column["name"] for column in inspect(db.get_bind()).get_columns("patient")}
    contact_columns = {
        "mobile": next(
            (name for name in ("mobile", "telephone", "phone") if name in columns),
            None,
        ),
        "email": next(
            (name for name in ("email", "courriel") if name in columns),
            None,
        ),
        "language": next(
            (name for name in ("language", "langue") if name in columns),
            None,
        ),
        "sms_consent": "sms_consent" if "sms_consent" in columns else None,
        "email_consent": "email_consent" if "email_consent" in columns else None,
    }
    selected = ", ".join(
        f'"{column}" AS "{field}"'
        for field, column in contact_columns.items()
        if column is not None
    )
    suffix = f", {selected}" if selected else ""
    patient = db.execute(
        text(
            "SELECT id,prenom,nom"
            f"{suffix} FROM patient WHERE id=:patient_id"
        ),
        {"patient_id": patient_id},
    ).mappings().first()
    if patient is None:
        return None
    return {
        "patient_id": patient["id"],
        "display_name": f"{patient['prenom']} {patient['nom']}".strip(),
        "mobile": patient.get("mobile"),
        "email": patient.get("email"),
        "language": patient.get("language"),
        "sms_consent": bool(patient.get("sms_consent", False)),
        "email_consent": bool(patient.get("email_consent", False)),
    }


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "module": "agenda-stock"}


IDENTITY_FIELDS = ("prenoms", "lieu_naissance", "nationalite", "adresse", "code_postal", "commune")


def _identity_values(payload) -> dict[str, object]:
    values: dict[str, object] = {
        name: " ".join((getattr(payload, name) or "").split()) or None for name in IDENTITY_FIELDS
    }
    sexe = (payload.sexe or "").strip().upper() or None
    if sexe not in (None, "M", "F", "X"):
        raise HTTPException(status_code=422, detail="Le sexe doit être M, F ou X.")
    values["sexe"] = sexe
    from main import normalize_national_number

    values["numero_national"] = normalize_national_number(getattr(payload, "numero_national", None))
    return values


def _store_photo(db: Session, patient_id: int, encoded: str | None) -> None:
    from main import decode_photo

    raw = decode_photo(encoded)
    if raw:
        db.execute(text("UPDATE patient SET photo=:photo WHERE id=:id"), {"photo": raw, "id": patient_id})


@router.get("/patients")
def list_patients(
    query: str = Query(default="", max_length=255),
    include_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    # Critères séparés par des virgules : tous doivent correspondre (ET) ;
    # chacun peut viser le nom/prénom, la date de naissance, le mobile, la commune ou « temporaire ».
    terms = [term.strip() for term in query.split(",") if term.strip()][:8]
    clauses: list[str] = []
    params: dict[str, object] = {"include_id": include_id}
    for index, term in enumerate(terms):
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params[f"t{index}"] = f"%{escaped}%"
        options = [
            f"lower(prenom || ' ' || nom) LIKE lower(:t{index})",
            f"lower(nom || ' ' || prenom) LIKE lower(:t{index})",
            f"lower(coalesce(commune,'')) LIKE lower(:t{index})",
        ]
        found = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", term)
        if found:
            try:
                params[f"d{index}"] = date(int(found[3]), int(found[2]), int(found[1]))
                options.append(f"date_naissance=:d{index}")
            except ValueError:
                pass
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", term):
            try:
                params[f"d{index}"] = date.fromisoformat(term)
                options.append(f"date_naissance=:d{index}")
            except ValueError:
                pass
        digits = "".join(ch for ch in term if ch.isdigit()).lstrip("0")
        if len(digits) >= 3 and re.fullmatch(r"[\d\s+./()-]+", term):
            params[f"m{index}"] = f"%{digits}%"
            options.append(f"regexp_replace(coalesce(mobile,''),'[^0-9]','','g') LIKE :m{index}")
        if re.fullmatch(r"temp(oraires?)?", term, re.IGNORECASE):
            options.append("is_temporary")
        clauses.append("(" + " OR ".join(options) + ")")
    condition = " AND ".join(clauses) if clauses else "TRUE"
    rows = db.execute(
        text(
            "SELECT id,prenom,nom,date_naissance,is_temporary FROM patient "
            f"WHERE id=:include_id OR ({condition}) ORDER BY nom,prenom,id LIMIT 100"
        ),
        params,
    ).mappings()
    return [
        {
            "id": row["id"],
            "display_name": f"{row['prenom']} {row['nom']}".strip(),
            "date_naissance": row["date_naissance"],
            "is_temporary": row["is_temporary"],
        }
        for row in rows
    ]


@router.get("/patients/match")
def match_patient(
    nom: str = Query(default="", max_length=255),
    prenom: str = Query(default="", max_length=255),
    date_naissance: date | None = Query(default=None),
    numero_national: str = Query(default="", max_length=20),
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> dict[str, object] | None:
    from main import normalize_national_number

    national = normalize_national_number(numero_national)
    if not national and not (nom.strip() and prenom.strip() and date_naissance):
        return None
    row = db.execute(
        text(
            "SELECT id,nom,prenom,date_naissance,mobile,email,prenoms,lieu_naissance,sexe,nationalite,"
            "adresse,code_postal,commune FROM patient WHERE NOT is_temporary AND ("
            "(CAST(:nn AS text) IS NOT NULL AND numero_national=:nn) OR "
            "(lower(nom)=lower(:nom) AND lower(prenom)=lower(:prenom) AND date_naissance=:dob)) "
            "ORDER BY (numero_national=:nn) DESC NULLS LAST, id LIMIT 1"
        ),
        {"nom": nom.strip(), "prenom": prenom.strip(), "dob": date_naissance, "nn": national},
    ).mappings().first()
    if not row:
        return None
    return {**row, "display_name": f"{row['prenom']} {row['nom']}".strip()}


@router.put("/patients/{patient_id}/identity")
def update_patient_identity(
    patient_id: int,
    payload: PatientReconcile,
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> dict[str, object]:
    values = _identity_values(payload)
    values = {key: value for key, value in values.items() if value is not None}
    if not values and not payload.photo:
        raise HTTPException(status_code=422, detail="Aucune information à mettre à jour.")
    exists = db.execute(text("SELECT 1 FROM patient WHERE id=:id"), {"id": patient_id}).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Patient introuvable.")
    if values:
        assignments = ",".join(f"{key}=:{key}" for key in values)
        db.execute(text(f"UPDATE patient SET {assignments} WHERE id=:id"), {"id": patient_id, **values})
    _store_photo(db, patient_id, payload.photo)
    db.commit()
    return {"id": patient_id, "updated": sorted(values) + (["photo"] if payload.photo else [])}


@router.get("/patients/temporary")
def list_temporary_patients(
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in db.execute(
            text(
                "SELECT p.id,p.prenom,p.nom,p.mobile,p.email,p.created_at,"
                "(SELECT count(*) FROM appointments a WHERE a.patient_id=p.id) AS appointments "
                "FROM patient p WHERE p.is_temporary ORDER BY p.created_at"
            )
        ).mappings()
    ]


@router.post("/patients/{patient_id}/reconcile")
def reconcile_patient(
    patient_id: int,
    payload: PatientReconcile,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict[str, object]:
    from main import PATIENT_EMAIL_PATTERN, PATIENT_LANGUAGES, PATIENT_MOBILE_PATTERN

    temp = db.execute(
        text("SELECT id,is_temporary FROM patient WHERE id=:id FOR UPDATE"), {"id": patient_id}
    ).mappings().first()
    if temp is None:
        raise HTTPException(status_code=404, detail="Patient introuvable.")
    if not temp["is_temporary"]:
        raise HTTPException(status_code=409, detail="Ce patient n'est pas temporaire.")

    if payload.target_id is not None:
        if payload.target_id == patient_id:
            raise HTTPException(status_code=422, detail="Choisissez un autre dossier patient.")
        target = db.execute(
            text("SELECT id FROM patient WHERE id=:id AND NOT is_temporary FOR UPDATE"),
            {"id": payload.target_id},
        ).first()
        if target is None:
            raise HTTPException(status_code=404, detail="Dossier patient cible introuvable.")
        for table in (
            "appointments", "follow_up_tasks", "notifications", "stock_movements", "patientdispensation",
        ):
            db.execute(
                text(f"UPDATE {table} SET patient_id=:to WHERE patient_id=:frm"),
                {"to": payload.target_id, "frm": patient_id},
            )
        db.execute(
            text(
                "UPDATE patient_communication_preferences SET patient_id=:to WHERE patient_id=:frm "
                "AND NOT EXISTS (SELECT 1 FROM patient_communication_preferences WHERE patient_id=:to)"
            ),
            {"to": payload.target_id, "frm": patient_id},
        )
        db.execute(text("DELETE FROM patient WHERE id=:id"), {"id": patient_id})
        db.commit()
        return {"id": payload.target_id, "merged": True}

    nom = (payload.nom or "").strip()
    prenom = (payload.prenom or "").strip()
    if not nom or not prenom or payload.date_naissance is None:
        raise HTTPException(status_code=422, detail="Nom, prénom et date de naissance sont obligatoires.")
    birth = payload.date_naissance
    if birth > date.today():
        raise HTTPException(status_code=422, detail="La date de naissance est dans le futur.")
    mobile = (payload.mobile or "").strip() or None
    email = (payload.email or "").strip() or None
    language = (payload.language or "").strip() or None
    if mobile and not PATIENT_MOBILE_PATTERN.match(mobile):
        raise HTTPException(status_code=422, detail="Numéro de mobile invalide.")
    if email and not PATIENT_EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=422, detail="Adresse e-mail invalide.")
    if language and language not in PATIENT_LANGUAGES:
        raise HTTPException(status_code=422, detail="Langue invalide.")
    duplicate = db.execute(
        text(
            "SELECT id FROM patient WHERE NOT is_temporary AND id<>:id AND lower(nom)=lower(:nom) "
            "AND lower(prenom)=lower(:prenom) AND date_naissance=:dob LIMIT 1"
        ),
        {"id": patient_id, "nom": nom, "prenom": prenom, "dob": birth},
    ).first()
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"Ce patient existe déjà dans la base (n° {duplicate[0]}) : fusionnez avec ce dossier.",
        )
    db.execute(
        text(
            "UPDATE patient SET nom=:nom,prenom=:prenom,date_naissance=:dob,mobile=:mobile,email=:email,"
            "language=:language,is_temporary=false,prenoms=:prenoms,lieu_naissance=:lieu_naissance,"
            "sexe=:sexe,nationalite=:nationalite,adresse=:adresse,code_postal=:code_postal,"
            "commune=:commune,numero_national=COALESCE(:numero_national,numero_national) WHERE id=:id"
        ),
        {
            "id": patient_id, "nom": nom, "prenom": prenom, "dob": birth,
            "mobile": mobile, "email": email, "language": language, **_identity_values(payload),
        },
    )
    _store_photo(db, patient_id, payload.photo)
    db.commit()
    return {"id": patient_id, "merged": False}


@router.post("/patients", status_code=201)
def create_patient(
    payload: PatientCreate,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict[str, object]:
    from main import PATIENT_EMAIL_PATTERN, PATIENT_LANGUAGES, PATIENT_MOBILE_PATTERN

    nom = payload.nom.strip()
    prenom = payload.prenom.strip()
    if not nom or not prenom:
        raise HTTPException(status_code=422, detail="Le nom et le prénom sont obligatoires.")
    temporary = payload.temporary
    # Un patient temporaire n'a pas de date de naissance connue : date technique, remplacée à la réconciliation.
    birth = payload.date_naissance or (date(1900, 1, 1) if temporary else None)
    if birth is None:
        raise HTTPException(status_code=422, detail="La date de naissance est obligatoire.")
    if birth > date.today():
        raise HTTPException(status_code=422, detail="La date de naissance est dans le futur.")
    mobile = (payload.mobile or "").strip() or None
    email = (payload.email or "").strip() or None
    language = (payload.language or "").strip() or None
    if mobile and not PATIENT_MOBILE_PATTERN.match(mobile):
        raise HTTPException(status_code=422, detail="Numéro de mobile invalide.")
    if email and not PATIENT_EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=422, detail="Adresse e-mail invalide.")
    if language and language not in PATIENT_LANGUAGES:
        raise HTTPException(status_code=422, detail="Langue invalide.")
    duplicate = None if temporary or payload.force else db.execute(
        text(
            "SELECT id FROM patient WHERE lower(nom)=lower(:nom) AND lower(prenom)=lower(:prenom) "
            "AND date_naissance=:dob LIMIT 1"
        ),
        {"nom": nom, "prenom": prenom, "dob": birth},
    ).first()
    if duplicate:
        raise HTTPException(status_code=409, detail=f"Ce patient existe déjà (n° {duplicate[0]}).")
    username = db.execute(
        text("SELECT username FROM useraccount WHERE id=:id"), {"id": user_id}
    ).scalar() or str(user_id)
    new_id = db.execute(
        text(
            "INSERT INTO patient (nom,prenom,date_naissance,mobile,email,language,sms_consent,email_consent,is_temporary,"
            "prenoms,lieu_naissance,sexe,nationalite,adresse,code_postal,commune,numero_national,created_by,created_at) "
            "VALUES (:nom,:prenom,:dob,:mobile,:email,:language,false,false,:temp,"
            ":prenoms,:lieu_naissance,:sexe,:nationalite,:adresse,:code_postal,:commune,:numero_national,:by,now()) RETURNING id"
        ),
        {
            "nom": nom, "prenom": prenom, "dob": birth, "mobile": mobile,
            "email": email, "language": language, "by": username, "temp": temporary,
            **_identity_values(payload),
        },
    ).scalar()
    _store_photo(db, new_id, payload.photo)
    db.commit()
    return {
        "id": new_id,
        "display_name": f"{prenom} {nom}".strip(),
        "date_naissance": birth,
        "is_temporary": temporary,
    }


@router.get("/patient-appointments")
def patient_appointments(
    query: str = Query(default="", max_length=255),
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    term = query.strip()
    if len(term) < 2:
        return []
    rows = db.execute(
        text(
            "SELECT a.id,a.patient_id,p.prenom||' '||p.nom AS patient_name,p.is_temporary AS patient_temporary,a.starts_at,a.ends_at,a.state,a.notes,"
            "r.name AS room,t.name AS appointment_type,d.display_name AS doctor "
            "FROM appointments a JOIN patient p ON p.id=a.patient_id "
            "LEFT JOIN rooms r ON r.id=a.room_id "
            "LEFT JOIN appointment_types t ON t.id=a.appointment_type_id "
            "LEFT JOIN people_resources d ON d.id=a.doctor_id "
            "WHERE lower(p.prenom||' '||p.nom) LIKE lower(:pattern) "
            "OR lower(p.nom||' '||p.prenom) LIKE lower(:pattern) "
            "OR CAST(p.id AS text)=:term OR CAST(p.date_naissance AS text) LIKE :pattern "
            "ORDER BY a.starts_at DESC LIMIT 300"
        ),
        {"pattern": f"%{term}%", "term": term},
    ).mappings()
    return [dict(row) for row in rows]


@router.get("/patients/{patient_id}/contact")
def patient_contact(
    patient_id: int,
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> dict[str, object]:
    contact = get_patient_contact(db, patient_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Patient introuvable.")
    return contact


def _status_rows(db: Session) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in db.execute(
            text(
                "SELECT code,label,color,edge,builtin,active FROM appointment_statuses "
                "ORDER BY sort_order,label"
            )
        ).mappings()
    ]


@router.get("/statuses")
def list_statuses(
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    return _status_rows(db)


@router.get("/status-colors")
def list_status_colors(
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> dict[str, str]:
    return {row["code"]: row["color"] for row in _status_rows(db)}


@router.post("/statuses", status_code=201, dependencies=[Depends(require_admin)])
def create_status(
    payload: StatusCreate,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    label = payload.label.strip()
    slug = re.sub(
        r"[^A-Z0-9]+", "_",
        unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().upper(),
    ).strip("_")[:30] or "STATUT"
    code = f"C_{slug}"
    existing = {row["code"] for row in _status_rows(db)}
    if label.lower() in {str(row["label"]).lower() for row in _status_rows(db)}:
        raise HTTPException(status_code=409, detail="Un statut porte déjà ce nom.")
    suffix = 1
    while code in existing:
        suffix += 1
        code = f"C_{slug}_{suffix}"
    assert re.fullmatch(r"[A-Z0-9_]+", code)
    # Une valeur d'enum doit être validée (commit) avant d'être utilisable : connexion autocommit dédiée.
    with db.get_bind().connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql(f"ALTER TYPE appointment_state ADD VALUE IF NOT EXISTS '{code}'")
    db.execute(
        text(
            "INSERT INTO appointment_statuses(code,label,color,edge,builtin,active,sort_order) "
            "VALUES(:code,:label,:color,:edge,false,true,"
            "(SELECT COALESCE(MAX(sort_order),0)+1 FROM appointment_statuses))"
        ),
        {"code": code, "label": label, "color": payload.color.upper(), "edge": payload.edge},
    )
    db.commit()
    return {"code": code}


@router.patch("/statuses/{code}", dependencies=[Depends(require_admin)])
def update_status(
    code: str,
    payload: StatusUpdate,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    fields = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not fields:
        raise HTTPException(status_code=422, detail="Aucun paramètre à modifier.")
    row = db.execute(
        text("SELECT builtin FROM appointment_statuses WHERE code=:code"), {"code": code}
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Statut introuvable.")
    if row[0] and fields.get("active") is False:
        raise HTTPException(status_code=422, detail="Un statut de base ne peut pas être désactivé.")
    if "label" in fields:
        fields["label"] = fields["label"].strip()
    if "color" in fields:
        fields["color"] = fields["color"].upper()
    assignments = ",".join(f"{name}=:{name}" for name in fields)
    db.execute(
        text(f"UPDATE appointment_statuses SET {assignments} WHERE code=:code"),
        {**fields, "code": code},
    )
    db.commit()
    return {"ok": True}


@router.get("/rooms")
def list_rooms(
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in db.execute(
            text("SELECT id,name,kind,color,capacity,max_parallel,default_state,show_doctor,show_status,sort_order FROM rooms WHERE active=true ORDER BY sort_order,name")
        ).mappings()
    ]


@router.put("/rooms-order", dependencies=[Depends(require_admin)])
def reorder_rooms(
    payload: RoomOrder,
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """Enregistre l'ordre des colonnes de salles ; les salles omises passent après."""
    for position, room_id in enumerate(payload.room_ids, start=1):
        db.execute(
            text("UPDATE rooms SET sort_order=:position WHERE id=:id AND active=true"),
            {"position": position, "id": room_id},
        )
    db.commit()
    return {"count": len(payload.room_ids)}


@router.post("/rooms", status_code=201, dependencies=[Depends(require_admin)])
def create_room(
    payload: RoomCreate,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        row_id = db.execute(
            text(
                "INSERT INTO rooms(name,kind,color,capacity,max_parallel,sort_order) "
                "VALUES(:name,:kind,:color,:capacity,:max_parallel,"
                "(SELECT COALESCE(MAX(sort_order),0)+1 FROM rooms)) RETURNING id"
            ),
            payload.model_dump(),
        ).scalar_one()
        db.commit()
        return {"id": str(row_id)}
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Une salle porte déjà ce nom.") from error


@router.patch("/rooms/{room_id}", dependencies=[Depends(require_admin)])
def update_room(
    room_id: UUID,
    payload: RoomUpdate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Règle la surréservation (rendez-vous simultanés) et le statut de transfert par défaut."""
    fields = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not fields:
        raise HTTPException(status_code=422, detail="Aucun paramètre à modifier.")
    if "default_state" in fields:
        fields["default_state"] = fields["default_state"] or None
    assignments = ",".join(f"{name}=:{name}" for name in fields)
    if "name" in fields:
        fields["name"] = fields["name"].strip()
        if not fields["name"]:
            raise HTTPException(status_code=422, detail="Le nom de la salle est obligatoire.")
    try:
        updated = db.execute(
            text(f"UPDATE rooms SET {assignments} WHERE id=:id AND active=true"),
            {**fields, "id": room_id},
        ).rowcount
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Une salle porte déjà ce nom.") from error
    if not updated:
        raise HTTPException(status_code=404, detail="Salle introuvable.")
    db.commit()
    return fields


@router.get("/agenda-groups")
def list_agenda_groups(
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in db.execute(
            text(
                "SELECT g.id,g.name,COALESCE(array_agg(gr.room_id) FILTER (WHERE gr.room_id IS NOT NULL),"
                "'{}') AS room_ids FROM agenda_groups g "
                "LEFT JOIN agenda_group_rooms gr ON gr.group_id=g.id "
                "GROUP BY g.id,g.name ORDER BY lower(g.name)"
            )
        ).mappings()
    ]


def _save_group_rooms(db: Session, group_id: UUID, room_ids: list[UUID]) -> None:
    db.execute(text("DELETE FROM agenda_group_rooms WHERE group_id=:id"), {"id": group_id})
    for room_id in dict.fromkeys(room_ids):
        db.execute(
            text("INSERT INTO agenda_group_rooms(group_id,room_id) VALUES(:g,:r)"),
            {"g": group_id, "r": room_id},
        )


@router.post("/agenda-groups", status_code=201, dependencies=[Depends(require_admin)])
def create_agenda_group(payload: AgendaGroupPayload, db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        group_id = db.execute(
            text("INSERT INTO agenda_groups(name) VALUES(:name) RETURNING id"),
            {"name": payload.name.strip()},
        ).scalar_one()
        _save_group_rooms(db, group_id, payload.room_ids)
        db.commit()
        return {"id": str(group_id)}
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Un groupe porte déjà ce nom ou une salle est invalide."
        ) from error


@router.put("/agenda-groups/{group_id}", dependencies=[Depends(require_admin)])
def update_agenda_group(
    group_id: UUID, payload: AgendaGroupPayload, db: Session = Depends(get_db)
) -> dict[str, bool]:
    try:
        updated = db.execute(
            text("UPDATE agenda_groups SET name=:name WHERE id=:id"),
            {"name": payload.name.strip(), "id": group_id},
        ).rowcount
        if not updated:
            raise HTTPException(status_code=404, detail="Groupe introuvable.")
        _save_group_rooms(db, group_id, payload.room_ids)
        db.commit()
        return {"ok": True}
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Un groupe porte déjà ce nom ou une salle est invalide."
        ) from error


@router.delete("/agenda-groups/{group_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_agenda_group(group_id: UUID, db: Session = Depends(get_db)) -> None:
    deleted = db.execute(text("DELETE FROM agenda_groups WHERE id=:id"), {"id": group_id}).rowcount
    if not deleted:
        raise HTTPException(status_code=404, detail="Groupe introuvable.")
    db.commit()


@router.get("/scheduling-blocks")
def list_scheduling_blocks(
    room_id: UUID | None = Query(default=None),
    resource_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in db.execute(
            text(
                f"SELECT {BLOCK_COLUMNS} {BLOCK_FROM} "
                "WHERE (CAST(:room_id AS uuid) IS NULL OR b.room_id=CAST(:room_id AS uuid)) "
                "AND (CAST(:resource_id AS uuid) IS NULL OR b.resource_id=CAST(:resource_id AS uuid)) "
                "ORDER BY r.name,b.weekday,b.start_time"
            ),
            {"room_id": room_id, "resource_id": resource_id},
        ).mappings()
    ]


@router.post("/scheduling-blocks", status_code=201, dependencies=[Depends(require_admin)])
def create_scheduling_blocks(
    payload: SchedulingBlockCreate,
    db: Session = Depends(get_db),
) -> dict[str, list[str]]:
    for weekday in payload.weekdays:
        if _block_overlap_exists(
            db, payload.room_id, payload.resource_id, weekday, payload.start_time,
            payload.end_time, payload.valid_from, payload.valid_to,
        ):
            raise HTTPException(
                status_code=409,
                detail="Une plage existante chevauche celle-ci pour la même salle et le même agenda.",
            )
    try:
        ids = [
            str(
                db.execute(
                    text(
                        "INSERT INTO scheduling_blocks(room_id,resource_id,weekday,start_time,"
                        "end_time,slot_minutes,label,color,valid_from,valid_to) "
                        "VALUES(:room_id,:resource_id,:weekday,:start_time,:end_time,"
                        ":slot_minutes,:label,:color,:valid_from,:valid_to) RETURNING id"
                    ),
                    {
                        **payload.model_dump(exclude={"weekdays"}),
                        "weekday": weekday,
                    },
                ).scalar_one()
            )
            for weekday in payload.weekdays
        ]
        db.commit()
        return {"ids": ids}
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Salle ou agenda invalide.") from error


@router.put(
    "/scheduling-blocks/{block_id}", dependencies=[Depends(require_admin)]
)
def update_scheduling_block(
    block_id: UUID,
    payload: SchedulingBlockUpdate,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    room_id = db.execute(
        text("SELECT room_id FROM scheduling_blocks WHERE id=:id"), {"id": block_id}
    ).scalar_one_or_none()
    if room_id is None:
        raise HTTPException(status_code=404, detail="Plage introuvable.")
    if payload.active and _block_overlap_exists(
        db, room_id, payload.resource_id, payload.weekday, payload.start_time,
        payload.end_time, payload.valid_from, payload.valid_to, block_id,
    ):
        raise HTTPException(
            status_code=409,
            detail="Une plage existante chevauche celle-ci pour la même salle et le même agenda.",
        )
    try:
        db.execute(
            text(
                "UPDATE scheduling_blocks SET resource_id=:resource_id,weekday=:weekday,"
                "start_time=:start_time,end_time=:end_time,slot_minutes=:slot_minutes,"
                "label=:label,color=:color,valid_from=:valid_from,valid_to=:valid_to,active=:active WHERE id=:id"
            ),
            {**payload.model_dump(), "id": block_id},
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Agenda invalide.") from error
    return {"id": str(block_id)}


@router.delete(
    "/scheduling-blocks/{block_id}", status_code=204, dependencies=[Depends(require_admin)]
)
def delete_scheduling_block(block_id: UUID, db: Session = Depends(get_db)) -> None:
    result = db.execute(text("DELETE FROM scheduling_blocks WHERE id=:id"), {"id": block_id})
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Plage introuvable.")


@router.get("/availability")
def availability(
    start: date = Query(...),
    end: date = Query(...),
    room_id: UUID | None = Query(default=None),
    doctor_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> dict[str, object]:
    """Plages réservables par jour sur [start, end[ ; les salles sans plage sont libres."""
    if end <= start or (end - start).days > 62:
        raise HTTPException(status_code=422, detail="Plage de dates invalide (62 jours maximum).")
    rows = db.execute(
        text(
            f"SELECT {BLOCK_COLUMNS} {BLOCK_FROM} WHERE b.active "
            "AND (CAST(:room_id AS uuid) IS NULL OR b.room_id=CAST(:room_id AS uuid)) "
            "AND (CAST(:doctor_id AS uuid) IS NULL OR b.resource_id IS NULL "
            "OR b.resource_id=CAST(:doctor_id AS uuid)) "
            "AND (b.valid_to IS NULL OR b.valid_to>=:start) "
            "AND (b.valid_from IS NULL OR b.valid_from<:end) "
            "ORDER BY b.start_time"
        ),
        {"room_id": room_id, "doctor_id": doctor_id, "start": start, "end": end},
    ).mappings().all()
    blocks = []
    day = start
    while day < end:
        for row in rows:
            if (
                row["weekday"] == day.weekday()
                and (row["valid_from"] is None or row["valid_from"] <= day)
                and (row["valid_to"] is None or row["valid_to"] >= day)
            ):
                blocks.append(
                    {
                        "date": day.isoformat(),
                        "room_id": row["room_id"],
                        "room": row["room"],
                        "resource_id": row["resource_id"],
                        "resource": row["resource"],
                        "start_time": row["start_time"],
                        "end_time": row["end_time"],
                        "slot_minutes": row["slot_minutes"],
                        "label": row["label"],
                        "color": row["color"] or row["room_color"],
                    }
                )
        day += timedelta(days=1)
    restricted = [
        str(value)
        for value in db.execute(
            text("SELECT DISTINCT room_id FROM scheduling_blocks WHERE active")
        ).scalars()
    ]
    return {"blocks": blocks, "restricted_room_ids": restricted}


def _parse_clock(value: str | None, default: time) -> time:
    if not value:
        return default
    try:
        return time.fromisoformat(value)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"Heure invalide : {value}") from error


@router.get("/suggestions")
def suggest_appointments(
    start: date = Query(...),
    end: date = Query(...),
    duration: int = Query(default=0, ge=0, le=480, description="0 = durée de la plage"),
    room_id: UUID | None = Query(default=None),
    group_id: UUID | None = Query(default=None),
    doctor_id: UUID | None = Query(default=None),
    weekdays: str | None = Query(default=None, description="Jours 0=lundi, séparés par des virgules"),
    time_from: str | None = Query(default=None),
    time_to: str | None = Query(default=None),
    preferred_time: str | None = Query(default=None),
    limit: int = Query(default=12, ge=1, le=50),
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> dict[str, object]:
    """Propose les meilleurs créneaux libres selon les critères (plages, salles, médecin, horaires)."""
    if end < start or (end - start).days > 61:
        raise HTTPException(status_code=422, detail="Période invalide (62 jours maximum).")
    try:
        allowed_days = (
            {int(item) for item in weekdays.split(",") if item.strip() != ""} if weekdays else set(range(7))
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Jours invalides.") from error
    window_start = _parse_clock(time_from, time(0, 0))
    window_end = _parse_clock(time_to, time(23, 59))
    preferred = _parse_clock(preferred_time, time(0, 0)) if preferred_time else None
    zone = ZoneInfo(AGENDA_TIMEZONE)
    now = datetime.now(zone)

    rooms = db.execute(
        text(
            "SELECT id,name,color,max_parallel FROM rooms WHERE active=true "
            "AND (CAST(:room_id AS uuid) IS NULL OR id=CAST(:room_id AS uuid)) "
            "AND (CAST(:group_id AS uuid) IS NULL OR CAST(:room_id AS uuid) IS NOT NULL OR id IN "
            "(SELECT room_id FROM agenda_group_rooms WHERE group_id=CAST(:group_id AS uuid))) ORDER BY name"
        ),
        {"room_id": room_id, "group_id": group_id},
    ).mappings().all()
    blocks = db.execute(
        text(
            f"SELECT {BLOCK_COLUMNS} {BLOCK_FROM} WHERE b.active "
            "AND (CAST(:doctor_id AS uuid) IS NULL OR b.resource_id IS NULL "
            "OR b.resource_id=CAST(:doctor_id AS uuid)) "
            "AND (b.valid_to IS NULL OR b.valid_to>=:start) "
            "AND (b.valid_from IS NULL OR b.valid_from<=:end)"
        ),
        {"doctor_id": doctor_id, "start": start, "end": end},
    ).mappings().all()
    restricted = {
        row for row in db.execute(
            text("SELECT DISTINCT room_id FROM scheduling_blocks WHERE active")
        ).scalars()
    }
    range_start = datetime.combine(start, time(0, 0), zone)
    range_end = datetime.combine(end + timedelta(days=1), time(0, 0), zone)
    busy = db.execute(
        text(
            "SELECT room_id,doctor_id,starts_at,ends_at FROM appointments "
            "WHERE starts_at<:end AND ends_at>:start "
            "AND state NOT IN ('CANCELLED','NO_SHOW','POSTPONED')"
        ),
        {"start": range_start, "end": range_end},
    ).mappings().all()
    doctors = {
        row["id"]: row["display_name"]
        for row in db.execute(text("SELECT id,display_name FROM people_resources")).mappings()
    }
    closures = db.execute(
        text(
            "SELECT e.room_id,e.resource_id,e.start_date,e.end_date,e.start_time,e.end_time FROM schedule_exceptions e "
            "JOIN schedule_exception_types t ON t.id=e.type_id "
            "WHERE t.blocks_booking AND e.start_date<=:e AND e.end_date>=:s"
        ),
        {"s": start, "e": end},
    ).mappings().all()

    def closed_on(day: date, room: UUID, doctor: UUID | None, begin: time, finish: time) -> bool:
        return any(
            c["start_date"] <= day <= c["end_date"]
            and begin < (c["end_time"] or time.max)
            and finish > (c["start_time"] or time.min)
            and (c["room_id"] is None or c["room_id"] == room)
            and (c["resource_id"] is None or c["resource_id"] == doctor)
            for c in closures
        )

    def occupied(room: UUID, doctor: UUID | None, s: datetime, e: datetime, limit: int) -> bool:
        room_items = [(i["starts_at"], i["ends_at"]) for i in busy if i["room_id"] == room]
        if exceeds_parallel(room_items, s, e, limit):
            return True
        if doctor is None:
            return False
        doctor_items = [(i["starts_at"], i["ends_at"]) for i in busy if i["doctor_id"] == doctor]
        return exceeds_parallel(doctor_items, s, e, limit)

    candidates: list[dict[str, object]] = []
    day = start
    while day <= end:
        if day.weekday() in allowed_days:
            for room in rooms:
                if room["id"] in restricted:
                    slots = [
                        (
                            time.fromisoformat(b["start_time"]), time.fromisoformat(b["end_time"]),
                            b["slot_minutes"], b["resource_id"], b["label"], b["color"] or b["room_color"],
                        )
                        for b in blocks
                        if b["room_id"] == room["id"] and b["weekday"] == day.weekday()
                        and (b["valid_from"] is None or b["valid_from"] <= day)
                        and (b["valid_to"] is None or b["valid_to"] >= day)
                    ]
                else:
                    slots = [(time(8, 0), time(18, 0), 30, None, None, room["color"])]
                for block_start, block_end, step, block_doctor, label, color in slots:
                    length = timedelta(minutes=duration or step)
                    cursor = datetime.combine(day, block_start)
                    limit_end = datetime.combine(day, block_end)
                    while cursor + length <= limit_end:
                        slot_end = cursor + length
                        s = cursor.replace(tzinfo=zone)
                        e = slot_end.replace(tzinfo=zone)
                        doctor = doctor_id or block_doctor
                        if (
                            s > now
                            and not closed_on(day, room["id"], doctor, cursor.time(), slot_end.time())
                            and cursor.time() >= window_start
                            and slot_end.time() <= window_end
                            and not occupied(room["id"], doctor, s, e, room["max_parallel"])
                        ):
                            candidates.append(
                                {
                                    "starts_at": s.isoformat(),
                                    "ends_at": e.isoformat(),
                                    "room_id": room["id"],
                                    "room": room["name"],
                                    "doctor_id": doctor,
                                    "doctor": doctors.get(doctor),
                                    "label": label,
                                    "color": color,
                                    "_sort": cursor,
                                }
                            )
                        cursor += timedelta(minutes=step)
        day += timedelta(days=1)

    if preferred is not None:
        target = preferred.hour * 60 + preferred.minute
        candidates.sort(
            key=lambda c: (
                c["_sort"].date(),
                abs(c["_sort"].hour * 60 + c["_sort"].minute - target),
                c["_sort"],
            )
        )
    else:
        candidates.sort(key=lambda c: c["_sort"])
    total = len(candidates)
    # Un seul créneau par horaire : les autres salles/médecins libres au même moment sont comptés.
    selected: list[dict[str, object]] = []
    by_start: dict[str, dict[str, object]] = {}
    for item in candidates:
        existing = by_start.get(item["starts_at"])
        if existing is not None:
            existing["alternatives"] += 1
            continue
        if len(selected) >= limit:
            continue
        item["alternatives"] = 0
        by_start[item["starts_at"]] = item
        selected.append(item)
    for item in selected:
        del item["_sort"]
    return {"total": total, "suggestions": selected}


@router.get("/appointment-types")
def list_appointment_types(
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in db.execute(
            text(
                "SELECT id,name,duration_minutes,color FROM appointment_types "
                "WHERE active=true ORDER BY name"
            )
        ).mappings()
    ]


@router.post(
    "/appointment-types",
    status_code=201,
    dependencies=[Depends(require_admin)],
)
def create_appointment_type(
    payload: AppointmentTypeCreate,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        row_id = db.execute(
            text(
                "INSERT INTO appointment_types(name,duration_minutes,color) "
                "VALUES(:name,:duration_minutes,:color) RETURNING id"
            ),
            payload.model_dump(),
        ).scalar_one()
        db.commit()
        return {"id": str(row_id)}
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Un type de rendez-vous porte déjà ce nom.",
        ) from error


@router.get("/resources")
def list_resources(
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in db.execute(
            text(
                "SELECT id,kind,display_name FROM people_resources "
                "WHERE active=true ORDER BY kind,display_name"
            )
        ).mappings()
    ]


@router.post("/resources", status_code=201, dependencies=[Depends(require_admin)])
def create_resource(
    payload: ResourceCreate,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        row_id = db.execute(
            text(
                "INSERT INTO people_resources(kind,display_name) "
                "VALUES(:kind,:display_name) RETURNING id"
            ),
            payload.model_dump(),
        ).scalar_one()
        db.commit()
        return {"id": str(row_id)}
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Cette ressource existe déjà pour ce type.",
        ) from error


def _patch_row(
    db: Session, table: str, row_id: UUID, fields: dict[str, object], label: str, duplicate: str
) -> dict[str, object]:
    if "name" in fields:
        fields["name"] = str(fields["name"]).strip()
    if "display_name" in fields:
        fields["display_name"] = str(fields["display_name"]).strip()
    if not fields or any(value == "" for value in fields.values()):
        raise HTTPException(status_code=422, detail="Paramètres de modification invalides.")
    assignments = ",".join(f"{name}=:{name}" for name in fields)
    try:
        updated = db.execute(
            text(f"UPDATE {table} SET {assignments} WHERE id=:id AND active=true"),
            {**fields, "id": row_id},
        ).rowcount
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=duplicate) from error
    if not updated:
        raise HTTPException(status_code=404, detail=f"{label} introuvable.")
    db.commit()
    return fields


@router.patch("/appointment-types/{type_id}", dependencies=[Depends(require_admin)])
def update_appointment_type(
    type_id: UUID,
    payload: AppointmentTypeUpdate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return _patch_row(
        db,
        "appointment_types",
        type_id,
        payload.model_dump(exclude_unset=True, exclude_none=True),
        "Type de rendez-vous",
        "Un type de rendez-vous porte déjà ce nom.",
    )


@router.patch("/resources/{resource_id}", dependencies=[Depends(require_admin)])
def update_resource(
    resource_id: UUID,
    payload: ResourceUpdate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return _patch_row(
        db,
        "people_resources",
        resource_id,
        payload.model_dump(exclude_unset=True, exclude_none=True),
        "Ressource",
        "Cette ressource existe déjà pour ce type.",
    )


LOCATION_TREE_CTE = (
    "WITH RECURSIVE tree(id,name,parent_id,path,depth,root_id) AS ("
    "SELECT id,name,parent_id,name::text,0,id FROM stock_locations "
    "WHERE parent_id IS NULL AND active=true "
    "UNION ALL "
    "SELECT l.id,l.name,l.parent_id,tree.path||' › '||l.name,tree.depth+1,tree.root_id "
    "FROM stock_locations l JOIN tree ON l.parent_id=tree.id WHERE l.active=true)"
)


@router.get("/stock-locations")
def list_stock_locations(
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    rows = db.execute(
        text(
            LOCATION_TREE_CTE
            + " SELECT t.id,t.name,t.parent_id,t.path,t.depth,"
            "COALESCE(array_agg(slr.room_id) FILTER (WHERE slr.room_id IS NOT NULL),'{}') AS room_ids "
            "FROM tree t LEFT JOIN stock_location_rooms slr ON slr.stock_location_id=t.id "
            "GROUP BY t.id,t.name,t.parent_id,t.path,t.depth ORDER BY t.path"
        )
    ).mappings()
    return [
        {**dict(row), "room_ids": [str(room_id) for room_id in row["room_ids"]]}
        for row in rows
    ]


def _replace_location_rooms(db: Session, location_id: UUID, room_ids: list[UUID]) -> None:
    db.execute(
        text("DELETE FROM stock_location_rooms WHERE stock_location_id=:id"),
        {"id": location_id},
    )
    for room_id in dict.fromkeys(room_ids):
        db.execute(
            text(
                "INSERT INTO stock_location_rooms(stock_location_id,room_id) "
                "VALUES(:l,:r)"
            ),
            {"l": location_id, "r": room_id},
        )


@router.post(
    "/stock-locations",
    status_code=201,
    dependencies=[Depends(require_admin)],
)
def create_stock_location(
    payload: StockLocationCreate,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        row_id = db.execute(
            text(
                "INSERT INTO stock_locations(name,parent_id) VALUES(:name,:parent_id) "
                "RETURNING id"
            ),
            {"name": payload.name, "parent_id": payload.parent_id},
        ).scalar_one()
        _replace_location_rooms(db, row_id, payload.room_ids)
        db.commit()
        return {"id": str(row_id)}
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Cet emplacement existe déjà à ce niveau, ou le parent/la salle est invalide.",
        ) from error


@router.put(
    "/stock-locations/{location_id}/rooms",
    dependencies=[Depends(require_admin)],
)
def set_stock_location_rooms(
    location_id: UUID,
    payload: StockLocationRooms,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    exists = db.execute(
        text("SELECT 1 FROM stock_locations WHERE id=:id AND active=true"),
        {"id": location_id},
    ).first()
    if exists is None:
        raise HTTPException(status_code=404, detail="Emplacement introuvable.")
    try:
        _replace_location_rooms(db, location_id, payload.room_ids)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Salle invalide.") from error
    return {"ok": True, "room_ids": [str(room_id) for room_id in dict.fromkeys(payload.room_ids)]}


@router.get("/stock-items")
def list_stock_items(
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in db.execute(
            text(
                "SELECT id,name,sku,track_lot,track_serial,consumable "
                "FROM stock_items WHERE active=true ORDER BY name"
            )
        ).mappings()
    ]


@router.post("/stock-items", status_code=201, dependencies=[Depends(require_admin)])
def create_stock_item(
    payload: StockItemCreate,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        row_id = db.execute(
            text(
                "INSERT INTO stock_items(name,sku,track_lot,track_serial,consumable) "
                "VALUES(:name,:sku,:track_lot,:track_serial,:consumable) RETURNING id"
            ),
            payload.model_dump(),
        ).scalar_one()
        db.commit()
        return {"id": str(row_id)}
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Un article porte déjà ce code SKU.",
        ) from error


@router.get("/stock-units")
def list_stock_units(
    room_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    # Une salle voit ses emplacements ainsi que leurs sous-emplacements.
    room_filter = (
        "AND su.stock_location_id IN (WITH RECURSIVE reach(id) AS ("
        "SELECT stock_location_id FROM stock_location_rooms WHERE room_id=:room_id "
        "UNION SELECT l.id FROM stock_locations l JOIN reach ON l.parent_id=reach.id) "
        "SELECT id FROM reach) "
        if room_id
        else ""
    )
    return [
        dict(row)
        for row in db.execute(
            text(
                LOCATION_TREE_CTE
                + " SELECT su.id,su.stock_location_id,t.path AS location_name,"
                "su.stock_item_id,si.name AS item_name,su.lot_number,su.serial_number,"
                "su.expires_on,su.quantity,su.reserved_quantity,"
                "(su.quantity-su.reserved_quantity) AS available_quantity "
                "FROM stock_units su JOIN tree t ON t.id=su.stock_location_id "
                "JOIN stock_items si ON si.id=su.stock_item_id "
                "WHERE si.active=true AND su.quantity>su.reserved_quantity "
                + room_filter
                + "ORDER BY t.path,si.name,su.expires_on NULLS LAST,su.lot_number"
            ),
            {"room_id": room_id} if room_id else {},
        ).mappings()
    ]


@router.post("/stock-units", status_code=201)
def create_stock_unit(
    payload: StockUnitCreate,
    db: Session = Depends(get_db),
    uid: int = Depends(require_admin),
) -> dict[str, str]:
    item = db.execute(
        text(
            "SELECT track_lot,track_serial FROM stock_items "
            "WHERE id=:id AND active=true"
        ),
        {"id": payload.stock_item_id},
    ).mappings().first()
    if item is None:
        raise HTTPException(status_code=404, detail="Article de stock introuvable.")
    if item["track_lot"] and not payload.lot_number:
        raise HTTPException(status_code=422, detail="Le numéro de lot est obligatoire.")
    if item["track_serial"] and not payload.serial_number:
        raise HTTPException(
            status_code=422,
            detail="Le numéro de série est obligatoire.",
        )
    if not item["track_serial"] and payload.serial_number:
        raise HTTPException(
            status_code=422,
            detail="Cet article ne suit pas les numéros de série.",
        )
    try:
        unit_id = db.execute(
            text(
                "INSERT INTO stock_units"
                "(stock_location_id,stock_item_id,lot_number,serial_number,expires_on,quantity) "
                "VALUES(:stock_location_id,:stock_item_id,:lot_number,:serial_number,"
                ":expires_on,:quantity) RETURNING id"
            ),
            payload.model_dump(),
        ).scalar_one()
        if payload.quantity > 0:
            db.execute(
                text(
                    "INSERT INTO stock_movements"
                    "(stock_unit_id,kind,quantity,created_by,comment) "
                    "VALUES(:unit_id,'ADJUSTMENT',:quantity,:user_id,'Stock initial')"
                ),
                {
                    "unit_id": unit_id,
                    "quantity": payload.quantity,
                    "user_id": uid,
                },
            )
        db.commit()
        return {"id": str(unit_id)}
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Emplacement, article ou numéro de série invalide/déjà utilisé.",
        ) from error


@router.get("/day-view")
def day_view(
    day: date = Query(...),
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    rows = db.execute(
        text(
            "SELECT a.id,a.patient_id,p.nom,p.prenom,p.is_temporary AS patient_temporary,a.starts_at,a.ends_at,a.state,"
            "a.phone,a.notes,r.name AS room,loc.name AS current_location,"
            "loc.kind AS location_kind,at.name AS appointment_type,"
            "d.display_name AS doctor,o.display_name AS operator,"
            "(SELECT rn.comment FROM recovery_notes rn "
            "WHERE rn.appointment_id=a.id ORDER BY rn.created_at DESC LIMIT 1) "
            "AS recovery_comment "
            "FROM appointments a JOIN patient p ON p.id=a.patient_id "
            "JOIN rooms r ON r.id=a.room_id "
            "LEFT JOIN rooms loc ON loc.id=a.current_location_id "
            "LEFT JOIN appointment_types at ON at.id=a.appointment_type_id "
            "LEFT JOIN people_resources d ON d.id=a.doctor_id "
            "LEFT JOIN people_resources o ON o.id=a.operator_id "
            "WHERE a.starts_at>=CAST(:day AS date) "
            "AND a.starts_at<(CAST(:day AS date)+interval '1 day') "
            "ORDER BY a.starts_at"
        ),
        {"day": day},
    ).mappings()
    result = []
    for row in rows:
        item = dict(row)
        item["patient_name"] = f"{row['prenom']} {row['nom']}".strip()
        del item["prenom"]
        del item["nom"]
        result.append(item)
    return result


@router.get("/appointment-history")
def appointment_history(
    patient_id: int | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    rows = db.execute(
        text(
            "SELECT a.id,a.patient_id,p.nom,p.prenom,p.is_temporary AS patient_temporary,a.starts_at,a.ends_at,a.state,"
            "a.notes,r.name AS room,at.name AS appointment_type,"
            "d.display_name AS doctor,o.display_name AS operator "
            "FROM appointments a JOIN patient p ON p.id=a.patient_id "
            "JOIN rooms r ON r.id=a.room_id "
            "LEFT JOIN appointment_types at ON at.id=a.appointment_type_id "
            "LEFT JOIN people_resources d ON d.id=a.doctor_id "
            "LEFT JOIN people_resources o ON o.id=a.operator_id "
            "WHERE (CAST(:patient_id AS integer) IS NULL OR a.patient_id=CAST(:patient_id AS integer)) "
            "AND (CAST(:date_from AS date) IS NULL OR a.starts_at>=CAST(:date_from AS date)) "
            "AND (CAST(:date_to AS date) IS NULL OR a.starts_at<CAST(:date_to AS date)+interval '1 day') "
            "AND (CAST(:q AS text) IS NULL OR (p.nom||' '||p.prenom||' '||COALESCE(at.name,'')||' '||"
            "COALESCE(d.display_name,'')||' '||r.name) ILIKE '%'||CAST(:q AS text)||'%') "
            "ORDER BY a.starts_at DESC LIMIT :limit"
        ),
        {"patient_id": patient_id, "date_from": date_from, "date_to": date_to,
         "q": (q or "").strip() or None, "limit": limit},
    ).mappings()
    result = []
    for row in rows:
        item = dict(row)
        item["patient_name"] = f"{row['prenom']} {row['nom']}".strip()
        del item["prenom"]
        del item["nom"]
        result.append(item)
    return result


@router.get("/range-view")
def range_view(
    start: datetime = Query(...),
    end: datetime = Query(...),
    room_id: UUID | None = Query(default=None),
    doctor_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    if end <= start or (end - start).days > 62:
        raise HTTPException(status_code=422, detail="Plage de dates invalide (62 jours maximum).")
    rows = db.execute(
        text(
            "SELECT a.id,a.patient_id,p.nom,p.prenom,p.is_temporary AS patient_temporary,a.starts_at,a.ends_at,a.state,"
            "a.notes,a.room_id,a.current_location_id,loc.name AS current_location,"
            "COALESCE(a.show_doctor,r.show_doctor) AS show_doctor,a.show_doctor AS own_show_doctor,"
            "COALESCE(a.show_status,r.show_status) AS show_status,"
            "a.appointment_type_id,a.doctor_id,a.operator_id,a.phone,"
            "r.name AS room,r.color AS room_color,"
            "at.name AS appointment_type,at.color AS type_color,"
            "d.display_name AS doctor,o.display_name AS operator,"
            "(SELECT count(*) FROM appointment_stock_usage asu WHERE asu.appointment_id=a.id "
            "AND (asu.reserved_quantity>0 OR asu.used_quantity>0)) AS planned_products,"
            "(SELECT string_agg(si.name||' x'||trim(trailing '.' FROM trim(trailing '0' FROM "
            "(asu.reserved_quantity+asu.used_quantity)::text))||CASE WHEN su.serial_number IS NOT NULL "
            "THEN ' ('||su.serial_number||')' ELSE '' END, E'\\n' ORDER BY si.name) "
            "FROM appointment_stock_usage asu JOIN stock_units su ON su.id=asu.stock_unit_id "
            "JOIN stock_items si ON si.id=su.stock_item_id WHERE asu.appointment_id=a.id "
            "AND (asu.reserved_quantity>0 OR asu.used_quantity>0)) AS planned_products_label "
            "FROM appointments a JOIN patient p ON p.id=a.patient_id "
            "JOIN rooms r ON r.id=a.room_id "
            "LEFT JOIN rooms loc ON loc.id=a.current_location_id "
            "LEFT JOIN appointment_types at ON at.id=a.appointment_type_id "
            "LEFT JOIN people_resources d ON d.id=a.doctor_id "
            "LEFT JOIN people_resources o ON o.id=a.operator_id "
            "WHERE a.starts_at<:end AND a.ends_at>:start "
            "AND (CAST(:room_id AS uuid) IS NULL OR a.room_id=CAST(:room_id AS uuid)) "
            "AND (CAST(:doctor_id AS uuid) IS NULL OR a.doctor_id=CAST(:doctor_id AS uuid)) "
            "ORDER BY a.starts_at"
        ),
        {"start": start, "end": end, "room_id": room_id, "doctor_id": doctor_id},
    ).mappings()
    result = []
    for row in rows:
        item = dict(row)
        item["patient_name"] = f"{row['prenom']} {row['nom']}".strip()
        del item["prenom"]
        del item["nom"]
        result.append(item)
    return result


@router.post("/appointments", status_code=201)
def create_appointment(
    payload: AppointmentCreate,
    db: Session = Depends(get_db),
    uid: int = Depends(get_current_user_id),
    admin: bool = Depends(is_admin_user),
) -> dict[str, str]:
    if get_patient_contact(db, payload.patient_id) is None:
        raise HTTPException(status_code=404, detail="Patient introuvable.")
    ensure_slot_allowed(db, payload.room_id, payload.doctor_id, payload.starts_at, payload.ends_at, admin)
    ensure_room_capacity(db, payload.room_id, payload.patient_id, payload.starts_at, payload.ends_at)
    try:
        appointment_id = db.execute(
            text(
                "INSERT INTO appointments"
                "(patient_id,appointment_type_id,room_id,doctor_id,operator_id,"
                "starts_at,ends_at,phone,notes,show_doctor,show_status,current_location_id,created_by) "
                "VALUES(:patient_id,:appointment_type_id,:room_id,:doctor_id,"
                ":operator_id,:starts_at,:ends_at,:phone,:notes,:show_doctor,:show_status,:room_id,:created_by) "
                "RETURNING id"
            ),
            {**payload.model_dump(), "created_by": uid},
        ).scalar_one()
        db.execute(
            text(
                "INSERT INTO appointment_status_history"
                "(appointment_id,new_state,changed_by,comment) "
                "VALUES(:appointment_id,'PLANNED',:user_id,'Création du rendez-vous')"
            ),
            {"appointment_id": appointment_id, "user_id": uid},
        )
        _audit(db, appointment_id, "CREATE", uid, None, _snapshot(db, appointment_id))
        db.commit()
        return {"id": str(appointment_id)}
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="La salle est occupée sur cette plage ou une référence est invalide.",
        ) from error


AUDIT_FIELDS = (
    "patient_id,appointment_type_id,room_id,doctor_id,operator_id,starts_at,ends_at,phone,notes,state"
)


def _snapshot(db: Session, appointment_id: UUID) -> dict | None:
    row = db.execute(
        text(f"SELECT {AUDIT_FIELDS} FROM appointments WHERE id=:id"), {"id": appointment_id}
    ).mappings().first()
    return json.loads(json.dumps(dict(row), default=str)) if row else None


def _audit(db: Session, appointment_id: UUID, action: str, uid: int,
           before: dict | None, after: dict | None) -> None:
    db.execute(
        text(
            "INSERT INTO appointment_audit(appointment_id,action,changed_by,before_data,after_data) "
            "VALUES(:id,:action,:uid,CAST(:before AS jsonb),CAST(:after AS jsonb))"
        ),
        {
            "id": appointment_id, "action": action, "uid": uid,
            "before": json.dumps(before) if before is not None else None,
            "after": json.dumps(after) if after is not None else None,
        },
    )


@router.get("/appointments/{appointment_id}/audit")
def appointment_audit(
    appointment_id: UUID,
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in db.execute(
            text(
                "SELECT a.id,a.action,a.changed_at,a.changed_by,u.username AS changed_by_name,"
                "a.before_data,a.after_data FROM appointment_audit a "
                "LEFT JOIN useraccount u ON u.id=a.changed_by "
                "WHERE a.appointment_id=:id ORDER BY a.changed_at DESC,a.id DESC"
            ),
            {"id": appointment_id},
        ).mappings()
    ]


@router.get("/appointments/{appointment_id}/day-follow-up")
def appointment_day_follow_up(
    appointment_id: UUID,
    day: date | None = Query(default=None),
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    """Chronologie complète d'une journée du patient : création, statuts, transferts, notes."""
    patient = db.execute(
        text("SELECT patient_id FROM appointments WHERE id=:id"), {"id": appointment_id}
    ).scalar()
    if patient is None:
        raise HTTPException(status_code=404, detail="Rendez-vous introuvable.")
    in_day = "(%s AT TIME ZONE 'Europe/Brussels')::date=COALESCE(CAST(:day AS date),(now() AT TIME ZONE 'Europe/Brussels')::date)"
    query = (
        "SELECT * FROM ("
        "SELECT s.changed_at AS at,'STATE' AS kind,s.appointment_id,s.old_state::text AS old_value,"
        "s.new_state::text AS new_value,s.comment,u.username AS by_name,NULL::text AS from_room,NULL::text AS to_room "
        "FROM appointment_status_history s JOIN appointments a ON a.id=s.appointment_id "
        "LEFT JOIN useraccount u ON u.id=s.changed_by "
        f"WHERE a.patient_id=:patient AND {in_day % 's.changed_at'} "
        "UNION ALL "
        "SELECT f.transferred_at,'TRANSFER',f.appointment_id,NULL,NULL,f.handover_comment,u.username,fr.name,tr.name "
        "FROM patient_flow_history f JOIN appointments a ON a.id=f.appointment_id "
        "LEFT JOIN rooms fr ON fr.id=f.from_location_id LEFT JOIN rooms tr ON tr.id=f.to_location_id "
        "LEFT JOIN useraccount u ON u.id=f.transferred_by "
        f"WHERE a.patient_id=:patient AND {in_day % 'f.transferred_at'} "
        "UNION ALL "
        "SELECT n.created_at,'NOTE',n.appointment_id,NULL,NULL,n.comment,u.username,NULL,r.name "
        "FROM recovery_notes n JOIN appointments a ON a.id=n.appointment_id "
        "LEFT JOIN rooms r ON r.id=n.room_id LEFT JOIN useraccount u ON u.id=n.created_by "
        f"WHERE a.patient_id=:patient AND {in_day % 'n.created_at'} "
        "UNION ALL "
        "SELECT x.changed_at,'AUDIT_'||x.action,x.appointment_id,NULL,NULL,NULL,u.username,NULL,NULL "
        "FROM appointment_audit x JOIN appointments a ON a.id=x.appointment_id "
        "LEFT JOIN useraccount u ON u.id=x.changed_by "
        f"WHERE a.patient_id=:patient AND x.action IN ('CREATE','MOVE','DELETE') AND {in_day % 'x.changed_at'}"
        ") t ORDER BY at"
    )
    return [dict(row) for row in db.execute(text(query), {"patient": patient, "day": day}).mappings()]


@router.get("/appointments/{appointment_id}/movements")
def appointment_movements(
    appointment_id: UUID,
    day: date | None = Query(default=None),
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    """Mouvements (transferts) du patient du rendez-vous pour une journée (aujourd'hui par défaut)."""
    patient = db.execute(
        text("SELECT patient_id FROM appointments WHERE id=:id"), {"id": appointment_id}
    ).scalar()
    if patient is None:
        raise HTTPException(status_code=404, detail="Rendez-vous introuvable.")
    return [
        dict(row)
        for row in db.execute(
            text(
                "SELECT f.id,f.appointment_id,f.transferred_at AS at,"
                "fr.name AS from_room,tr.name AS to_room,f.handover_comment AS comment,"
                "u.username AS by_name,s.new_state AS state "
                "FROM patient_flow_history f "
                "JOIN appointments a ON a.id=f.appointment_id "
                "LEFT JOIN rooms fr ON fr.id=f.from_location_id "
                "LEFT JOIN rooms tr ON tr.id=f.to_location_id "
                "LEFT JOIN useraccount u ON u.id=f.transferred_by "
                "LEFT JOIN appointment_status_history s ON s.appointment_id=f.appointment_id "
                "AND s.changed_at=f.transferred_at "
                "WHERE a.patient_id=:patient "
                "AND (f.transferred_at AT TIME ZONE 'Europe/Brussels')::date="
                "COALESCE(CAST(:day AS date),(now() AT TIME ZONE 'Europe/Brussels')::date) "
                "ORDER BY f.transferred_at,f.id"
            ),
            {"patient": patient, "day": day},
        ).mappings()
    ]


@router.put("/appointments/{appointment_id}")
def update_appointment(
    appointment_id: UUID,
    payload: AppointmentCreate,
    db: Session = Depends(get_db),
    uid: int = Depends(get_current_user_id),
    admin: bool = Depends(is_admin_user),
) -> dict[str, bool]:
    """Modifie ou déplace un rendez-vous (date, heure, salle, médecin, notes…)."""
    current = db.execute(
        text("SELECT state,starts_at,ends_at,room_id,patient_id FROM appointments WHERE id=:id"),
        {"id": appointment_id},
    ).mappings().first()
    if current is None:
        raise HTTPException(status_code=404, detail="Rendez-vous introuvable.")
    if current["state"] in ("CANCELLED", "NO_SHOW", "DISCHARGED"):
        raise HTTPException(status_code=409, detail="Ce rendez-vous est clôturé et ne peut plus être modifié.")
    if get_patient_contact(db, payload.patient_id) is None:
        raise HTTPException(status_code=404, detail="Patient introuvable.")
    moved = (
        current["starts_at"] != payload.starts_at
        or current["ends_at"] != payload.ends_at
        or current["room_id"] != payload.room_id
    )
    if moved:
        ensure_slot_allowed(db, payload.room_id, payload.doctor_id, payload.starts_at, payload.ends_at, admin)
    if moved or current["patient_id"] != payload.patient_id:
        ensure_room_capacity(
            db, payload.room_id, payload.patient_id, payload.starts_at, payload.ends_at, appointment_id
        )
    try:
        before = _snapshot(db, appointment_id)
        db.execute(
            text(
                "UPDATE appointments SET patient_id=:patient_id,appointment_type_id=:appointment_type_id,"
                "room_id=:room_id,"
                "current_location_id=CASE WHEN room_id IS DISTINCT FROM :room_id "
                "THEN :room_id ELSE current_location_id END,doctor_id=:doctor_id,"
                "operator_id=:operator_id,starts_at=:starts_at,"
                "ends_at=:ends_at,phone=:phone,notes=:notes,"
                "show_doctor=COALESCE(:show_doctor,show_doctor,CASE WHEN room_id IS DISTINCT FROM :room_id "
                "THEN (SELECT r0.show_doctor FROM rooms r0 WHERE r0.id=appointments.room_id) END),"
                "show_status=COALESCE(:show_status,show_status,CASE WHEN room_id IS DISTINCT FROM :room_id "
                "THEN (SELECT r0.show_status FROM rooms r0 WHERE r0.id=appointments.room_id) END),"
                "updated_at=now(),version=version+1 "
                "WHERE id=:id"
            ),
            {**payload.model_dump(), "id": appointment_id},
        )
        _audit(db, appointment_id, "MOVE" if moved else "UPDATE", uid, before, _snapshot(db, appointment_id))
        db.commit()
        return {"ok": True}
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="La salle est occupée sur cette plage ou une référence est invalide.",
        ) from error


@router.delete("/appointments/{appointment_id}", status_code=204)
def delete_appointment(
    appointment_id: UUID,
    db: Session = Depends(get_db),
    uid: int = Depends(get_current_user_id),
) -> None:
    before = _snapshot(db, appointment_id)
    state = before["state"] if before else None
    if state is None:
        raise HTTPException(status_code=404, detail="Rendez-vous introuvable.")
    if state != "PLANNED":
        raise HTTPException(
            status_code=409,
            detail="Seul un rendez-vous encore « Planifié » peut être supprimé ; utilisez « Annuler » sinon.",
        )
    try:
        release_planned_stock(db, appointment_id)
        db.execute(text("DELETE FROM appointments WHERE id=:id"), {"id": appointment_id})
        _audit(db, appointment_id, "DELETE", uid, before, None)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Ce rendez-vous est lié à d'autres données (suivi, stock) : annulez-le plutôt.",
        ) from error


@router.get("/deleted-appointments")
def list_deleted_appointments(
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    rows = db.execute(
        text(
            "SELECT deleted.id,deleted.changed_at,deleted.before_data->>'patient_id' AS patient_id,"
            "p.prenom||' '||p.nom AS patient_name,deleted.before_data->>'starts_at' AS starts_at,"
            "deleted.before_data->>'ends_at' AS ends_at,deleted.before_data->>'room_id' AS room_id,"
            "r.name AS room,deleted.before_data->>'appointment_type_id' AS appointment_type_id,"
            "at.name AS appointment_type,deleted.before_data->>'doctor_id' AS doctor_id,"
            "d.display_name AS doctor,deleted.before_data->>'operator_id' AS operator_id,"
            "deleted.before_data->>'notes' AS notes,deleted.before_data->>'phone' AS phone "
            "FROM (SELECT DISTINCT ON (a.appointment_id) a.appointment_id AS id,a.action,a.changed_at,"
            "a.before_data FROM appointment_audit a ORDER BY a.appointment_id,a.changed_at DESC,a.id DESC) deleted "
            "JOIN patient p ON p.id=(deleted.before_data->>'patient_id')::integer "
            "JOIN rooms r ON r.id=(deleted.before_data->>'room_id')::uuid "
            "LEFT JOIN appointment_types at ON at.id=NULLIF(deleted.before_data->>'appointment_type_id','')::uuid "
            "LEFT JOIN people_resources d ON d.id=NULLIF(deleted.before_data->>'doctor_id','')::uuid "
            "WHERE deleted.action='DELETE' AND NOT EXISTS "
            "(SELECT 1 FROM appointments current WHERE current.id=deleted.id) "
            "ORDER BY deleted.changed_at DESC LIMIT 500"
        )
    ).mappings()
    return [dict(row) for row in rows]


@router.post("/deleted-appointments/{appointment_id}/restore", status_code=201)
def restore_deleted_appointment(
    appointment_id: UUID,
    payload: RestoreDeletedAppointment,
    db: Session = Depends(get_db),
    uid: int = Depends(get_current_user_id),
    admin: bool = Depends(is_admin_user),
) -> dict[str, str]:
    deleted = db.execute(
        text(
            "SELECT a.before_data FROM appointment_audit a WHERE a.appointment_id=:id AND a.action='DELETE' "
            "AND NOT EXISTS (SELECT 1 FROM appointments current WHERE current.id=:id) "
            "ORDER BY a.changed_at DESC,a.id DESC LIMIT 1"
        ),
        {"id": appointment_id},
    ).scalar_one_or_none()
    if not deleted:
        raise HTTPException(status_code=404, detail="Rendez-vous supprimé introuvable ou déjà récupéré.")
    if not deleted.get("patient_id") or not deleted.get("room_id"):
        raise HTTPException(status_code=409, detail="Les données nécessaires à la récupération sont incomplètes.")
    original_start = datetime.fromisoformat(deleted["starts_at"])
    original_end = datetime.fromisoformat(deleted["ends_at"])
    start = payload.starts_at or original_start
    if start.tzinfo is None:
        start = start.replace(tzinfo=ZoneInfo(AGENDA_TIMEZONE))
    duration = timedelta(minutes=payload.duration_minutes) if payload.duration_minutes else original_end - original_start
    end = start + duration
    room_id = payload.room_id or UUID(deleted["room_id"])
    if "doctor_id" in payload.model_fields_set:
        doctor_id = payload.doctor_id
    else:
        doctor_id = UUID(deleted["doctor_id"]) if deleted.get("doctor_id") else None
    appointment_type_id = payload.appointment_type_id or deleted.get("appointment_type_id")
    patient_id = int(deleted["patient_id"])
    if get_patient_contact(db, patient_id) is None:
        raise HTTPException(status_code=409, detail="Le patient associé n'existe plus.")
    ensure_slot_allowed(db, room_id, doctor_id, start, end, admin)
    ensure_room_capacity(db, room_id, patient_id, start, end)
    try:
        db.execute(
            text(
                "INSERT INTO appointments(id,patient_id,appointment_type_id,room_id,doctor_id,operator_id,"
                "starts_at,ends_at,phone,notes,current_location_id,created_by) "
                "VALUES(:id,:patient_id,:appointment_type_id,:room_id,:doctor_id,:operator_id,"
                ":starts_at,:ends_at,:phone,:notes,:room_id,:created_by)"
            ),
            {
                "id": appointment_id,
                "patient_id": patient_id,
                "appointment_type_id": appointment_type_id,
                "room_id": room_id,
                "doctor_id": doctor_id,
                "operator_id": deleted.get("operator_id"),
                "starts_at": start,
                "ends_at": end,
                "phone": deleted.get("phone"),
                "notes": deleted.get("notes"),
                "created_by": uid,
            },
        )
        db.execute(
            text(
                "INSERT INTO appointment_status_history(appointment_id,new_state,changed_by,comment) "
                "VALUES(:id,'PLANNED',:uid,'Rendez-vous récupéré depuis la corbeille')"
            ),
            {"id": appointment_id, "uid": uid},
        )
        _audit(db, appointment_id, "CREATE", uid, None, _snapshot(db, appointment_id))
        db.commit()
        return {"id": str(appointment_id)}
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Impossible de récupérer ce rendez-vous : la salle est occupée ou une référence n'est plus valide.",
        ) from error


@router.post("/appointments/{appointment_id}/transfer")
def transfer_patient(
    appointment_id: UUID,
    payload: TransferRequest,
    db: Session = Depends(get_db),
    uid: int = Depends(get_current_user_id),
) -> dict[str, object]:
    return transfer(db, appointment_id, payload, uid)


@router.post("/appointments/{appointment_id}/status")
def set_status(
    appointment_id: UUID,
    payload: StatusRequest,
    db: Session = Depends(get_db),
    uid: int = Depends(get_current_user_id),
) -> dict[str, bool]:
    if not db.execute(
        text("SELECT 1 FROM appointment_statuses WHERE code=:code AND active"),
        {"code": payload.new_state},
    ).first():
        raise HTTPException(status_code=422, detail="Statut inconnu ou désactivé.")
    try:
        appointment = db.execute(
            text("SELECT state FROM appointments WHERE id=:id FOR UPDATE"),
            {"id": appointment_id},
        ).mappings().first()
        if not appointment:
            raise HTTPException(status_code=404, detail="Rendez-vous introuvable.")
        db.execute(
            text(
                "INSERT INTO appointment_status_history"
                "(appointment_id,old_state,new_state,changed_by,comment) "
                "VALUES(:appointment_id,:old_state,:new_state,:user_id,:comment)"
            ),
            {
                "appointment_id": appointment_id,
                "old_state": appointment["state"],
                "new_state": payload.new_state,
                "user_id": uid,
                "comment": payload.comment,
            },
        )
        db.execute(
            text(
                "UPDATE appointments SET state=CAST(:state AS appointment_state),"
                "updated_at=now(),version=version+1 WHERE id=:id"
            ),
            {"state": payload.new_state, "id": appointment_id},
        )
        apply_state_to_planned_stock(db, appointment_id, payload.new_state, uid)
        db.commit()
        if str(appointment["state"]) == "TO_CONFIRM" and payload.new_state != "TO_CONFIRM":
            from .portal import notify_status_change

            notify_status_change(appointment_id, "TO_CONFIRM", payload.new_state)
        return {"ok": True}
    except Exception:
        db.rollback()
        raise


@router.get("/follow-ups")
def followups(
    state: str = Query(default="TO_PLAN", pattern="^(TO_PLAN|PLANNED|CANCELLED|DONE)$"),
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in db.execute(
            text(
                "SELECT * FROM follow_up_tasks WHERE state=CAST(:state AS follow_up_state) "
                "ORDER BY target_date NULLS LAST,created_at"
            ),
            {"state": state},
        ).mappings()
    ]


# ---- Exceptions de planning (fermeture, congé, malade, ...) ----
EXCEPTION_SELECT = (
    "SELECT e.id,e.type_id,t.name AS type,t.color,t.blocks_booking,e.room_id,r.name AS room,"
    "e.resource_id,p.display_name AS resource,e.start_date,e.end_date,to_char(e.start_time,'HH24:MI') AS start_time,"
    "to_char(e.end_time,'HH24:MI') AS end_time,e.note "
    "FROM schedule_exceptions e JOIN schedule_exception_types t ON t.id=e.type_id "
    "LEFT JOIN rooms r ON r.id=e.room_id LEFT JOIN people_resources p ON p.id=e.resource_id "
)


def _count_affected(db: Session, payload: ExceptionPayload) -> int:
    return db.execute(
        text(
            "SELECT count(*) FROM appointments WHERE state NOT IN ('CANCELLED','NO_SHOW','POSTPONED') "
            "AND (starts_at AT TIME ZONE :tz)::date BETWEEN :s AND :e "
            "AND (CAST(:room AS uuid) IS NULL OR room_id=CAST(:room AS uuid)) "
            "AND (CAST(:res AS uuid) IS NULL OR doctor_id=CAST(:res AS uuid)) "
            "AND (starts_at AT TIME ZONE :tz)::time < COALESCE(CAST(:te AS time), TIME '23:59:59.999999') "
            "AND ((ends_at AT TIME ZONE :tz)::date > (starts_at AT TIME ZONE :tz)::date "
            "OR (ends_at AT TIME ZONE :tz)::time > COALESCE(CAST(:ts AS time), TIME '00:00'))"
        ),
        {"tz": AGENDA_TIMEZONE, "s": payload.start_date, "e": payload.end_date,
         "room": payload.room_id, "res": payload.resource_id,
         "ts": payload.start_time, "te": payload.end_time},
    ).scalar_one()


@router.get("/exception-types")
def list_exception_types(
    db: Session = Depends(get_db), _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    return [
        dict(row) for row in db.execute(
            text("SELECT id,name,color,blocks_booking FROM schedule_exception_types ORDER BY name")
        ).mappings()
    ]


@router.post("/exception-types", status_code=201, dependencies=[Depends(require_admin)])
def create_exception_type(payload: ExceptionTypePayload, db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        row = db.execute(
            text(
                "INSERT INTO schedule_exception_types(name,color,blocks_booking) "
                "VALUES(:name,:color,:blocks) RETURNING id,name,color,blocks_booking"
            ),
            {"name": payload.name.strip(), "color": payload.color, "blocks": payload.blocks_booking},
        ).mappings().one()
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ce libellé existe déjà.") from error
    return dict(row)


@router.put("/exception-types/{type_id}", dependencies=[Depends(require_admin)])
def update_exception_type(
    type_id: UUID, payload: ExceptionTypePayload, db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        row = db.execute(
            text(
                "UPDATE schedule_exception_types SET name=:name,color=:color,blocks_booking=:blocks "
                "WHERE id=:id RETURNING id,name,color,blocks_booking"
            ),
            {"id": type_id, "name": payload.name.strip(), "color": payload.color,
             "blocks": payload.blocks_booking},
        ).mappings().first()
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ce libellé existe déjà.") from error
    if not row:
        raise HTTPException(status_code=404, detail="Libellé introuvable.")
    return dict(row)


@router.delete("/exception-types/{type_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_exception_type(type_id: UUID, db: Session = Depends(get_db)) -> None:
    try:
        deleted = db.execute(
            text("DELETE FROM schedule_exception_types WHERE id=:id"), {"id": type_id}
        ).rowcount
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Ce libellé est utilisé par des exceptions : supprimez-les d'abord."
        ) from error
    if not deleted:
        raise HTTPException(status_code=404, detail="Libellé introuvable.")


@router.get("/schedule-exceptions")
def list_schedule_exceptions(
    start: date = Query(...),
    end: date = Query(...),
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    if end < start or (end - start).days > 400:
        raise HTTPException(status_code=422, detail="Période invalide.")
    return [
        dict(row) for row in db.execute(
            text(EXCEPTION_SELECT + "WHERE e.start_date<=:e AND e.end_date>=:s ORDER BY e.start_date,e.end_date,t.name"),
            {"s": start, "e": end},
        ).mappings()
    ]


@router.post("/schedule-exceptions", status_code=201)
def create_schedule_exception(
    payload: ExceptionPayload, db: Session = Depends(get_db), uid: int = Depends(require_admin),
) -> dict[str, object]:
    try:
        new_id = db.execute(
            text(
                "INSERT INTO schedule_exceptions(type_id,room_id,resource_id,start_date,end_date,start_time,end_time,note,created_by) "
                "VALUES(:type,:room,:res,:s,:e,:ts,:te,:note,:uid) RETURNING id"
            ),
            {"type": payload.type_id, "room": payload.room_id, "res": payload.resource_id,
             "s": payload.start_date, "e": payload.end_date, "ts": payload.start_time,
             "te": payload.end_time, "note": (payload.note or "").strip() or None, "uid": uid},
        ).scalar_one()
        affected = _count_affected(db, payload)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail="Libellé, salle ou médecin invalide.") from error
    row = db.execute(text(EXCEPTION_SELECT + "WHERE e.id=:id"), {"id": new_id}).mappings().one()
    return {**dict(row), "affected_appointments": affected}


@router.put("/schedule-exceptions/{exception_id}", dependencies=[Depends(require_admin)])
def update_schedule_exception(
    exception_id: UUID, payload: ExceptionPayload, db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        updated = db.execute(
            text(
                "UPDATE schedule_exceptions SET type_id=:type,room_id=:room,resource_id=:res,"
                "start_date=:s,end_date=:e,start_time=:ts,end_time=:te,note=:note WHERE id=:id"
            ),
            {"id": exception_id, "type": payload.type_id, "room": payload.room_id,
             "res": payload.resource_id, "s": payload.start_date, "e": payload.end_date,
             "ts": payload.start_time, "te": payload.end_time,
             "note": (payload.note or "").strip() or None},
        ).rowcount
        affected = _count_affected(db, payload)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail="Libellé, salle ou médecin invalide.") from error
    if not updated:
        raise HTTPException(status_code=404, detail="Exception introuvable.")
    row = db.execute(text(EXCEPTION_SELECT + "WHERE e.id=:id"), {"id": exception_id}).mappings().one()
    return {**dict(row), "affected_appointments": affected}


@router.delete("/schedule-exceptions/{exception_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_schedule_exception(exception_id: UUID, db: Session = Depends(get_db)) -> None:
    deleted = db.execute(
        text("DELETE FROM schedule_exceptions WHERE id=:id"), {"id": exception_id}
    ).rowcount
    db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Exception introuvable.")

# ---- Produits prévus pour une intervention (ajoutés via QR code, consommés à la fin) ----
PLANNED_STOCK_SELECT = (
    "SELECT u.stock_unit_id,si.name AS item_name,su.lot_number,su.serial_number,su.expires_on,"
    "u.planned_quantity,u.reserved_quantity,u.used_quantity,u.returned_quantity "
    "FROM appointment_stock_usage u JOIN stock_units su ON su.id=u.stock_unit_id "
    "JOIN stock_items si ON si.id=su.stock_item_id WHERE u.appointment_id=:a ORDER BY si.name,su.serial_number"
)


def _planned_stock(db: Session, appointment_id: UUID) -> list[dict[str, object]]:
    return [dict(row) for row in db.execute(text(PLANNED_STOCK_SELECT), {"a": appointment_id}).mappings()]


def _resolve_stock_unit(db: Session, payload: PlannedStockAdd) -> UUID:
    if payload.stock_unit_id:
        return payload.stock_unit_id
    code = (payload.code or "").strip()
    by_serial = db.execute(
        text(
            "SELECT su.id FROM stock_units su JOIN stock_items si ON si.id=su.stock_item_id "
            "WHERE si.active AND trim(su.serial_number)=:c ORDER BY (su.quantity-su.reserved_quantity)>0 DESC LIMIT 1"
        ),
        {"c": code},
    ).scalar_one_or_none()
    if by_serial:
        return by_serial
    by_item = db.execute(
        text(
            "SELECT su.id FROM stock_units su JOIN stock_items si ON si.id=su.stock_item_id "
            "LEFT JOIN stockitem li ON li.id=si.legacy_stock_item_id "
            "WHERE si.active AND (trim(si.sku)=:c OR trim(li.code_barres)=:c OR trim(li.numero_serie)=:c) "
            "AND su.quantity>su.reserved_quantity "
            "ORDER BY su.expires_on NULLS LAST,su.id LIMIT 1"
        ),
        {"c": code},
    ).scalar_one_or_none()
    if by_item:
        return by_item
    raise HTTPException(status_code=404, detail="Code inconnu : aucun produit du stock ne correspond.")


@router.get("/appointments/{appointment_id}/planned-stock")
def list_planned_stock(
    appointment_id: UUID, db: Session = Depends(get_db), _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    return _planned_stock(db, appointment_id)


@router.post("/appointments/{appointment_id}/planned-stock", status_code=201)
def add_planned_stock(
    appointment_id: UUID,
    payload: PlannedStockAdd,
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    try:
        state = db.execute(
            text("SELECT state FROM appointments WHERE id=:id FOR UPDATE"), {"id": appointment_id}
        ).scalar_one_or_none()
        if state is None:
            raise HTTPException(status_code=404, detail="Rendez-vous introuvable.")
        if state in CLOSED_STOCK_STATES:
            raise HTTPException(status_code=409, detail="Ce rendez-vous est clôturé : ajout impossible.")
        unit_id = _resolve_stock_unit(db, payload)
        unit = db.execute(
            text(
                "SELECT su.quantity,su.reserved_quantity,si.name FROM stock_units su "
                "JOIN stock_items si ON si.id=su.stock_item_id WHERE su.id=:id FOR UPDATE"
            ),
            {"id": unit_id},
        ).mappings().first()
        if not unit:
            raise HTTPException(status_code=404, detail="Unité de stock introuvable.")
        if unit["quantity"] - unit["reserved_quantity"] < payload.quantity:
            raise HTTPException(
                status_code=409, detail=f"Stock disponible insuffisant pour « {unit['name']} ».",
            )
        db.execute(
            text("UPDATE stock_units SET reserved_quantity=reserved_quantity+:q WHERE id=:id"),
            {"q": payload.quantity, "id": unit_id},
        )
        db.execute(
            text(
                "INSERT INTO appointment_stock_usage(appointment_id,stock_unit_id,planned_quantity,reserved_quantity) "
                "VALUES(:a,:s,:q,:q) ON CONFLICT(appointment_id,stock_unit_id) DO UPDATE SET "
                "planned_quantity=appointment_stock_usage.planned_quantity+excluded.planned_quantity,"
                "reserved_quantity=appointment_stock_usage.reserved_quantity+excluded.reserved_quantity"
            ),
            {"a": appointment_id, "s": unit_id, "q": payload.quantity},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _planned_stock(db, appointment_id)


@router.delete("/appointments/{appointment_id}/planned-stock/{stock_unit_id}")
def remove_planned_stock(
    appointment_id: UUID,
    stock_unit_id: UUID,
    db: Session = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> list[dict[str, object]]:
    try:
        line = db.execute(
            text(
                "SELECT id,reserved_quantity,planned_quantity,used_quantity,returned_quantity "
                "FROM appointment_stock_usage WHERE appointment_id=:a AND stock_unit_id=:s FOR UPDATE"
            ),
            {"a": appointment_id, "s": stock_unit_id},
        ).mappings().first()
        if not line or line["reserved_quantity"] <= 0:
            raise HTTPException(status_code=404, detail="Aucun produit prévu à retirer.")
        db.execute(
            text("UPDATE stock_units SET reserved_quantity=GREATEST(reserved_quantity-:q,0) WHERE id=:id"),
            {"q": line["reserved_quantity"], "id": stock_unit_id},
        )
        remaining = line["planned_quantity"] - line["reserved_quantity"]
        if remaining <= 0 and line["used_quantity"] == 0 and line["returned_quantity"] == 0:
            db.execute(text("DELETE FROM appointment_stock_usage WHERE id=:id"), {"id": line["id"]})
        else:
            db.execute(
                text(
                    "UPDATE appointment_stock_usage SET reserved_quantity=0,"
                    "planned_quantity=GREATEST(planned_quantity-:q,0) WHERE id=:id"
                ),
                {"q": line["reserved_quantity"], "id": line["id"]},
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _planned_stock(db, appointment_id)
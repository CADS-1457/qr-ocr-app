from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .schemas import TransferRequest


def transfer(
    db: Session,
    appointment_id: UUID,
    payload: TransferRequest,
    user_id: int,
) -> dict[str, object]:
    try:
        appointment = db.execute(
            text(
                "SELECT id,patient_id,state,current_location_id "
                "FROM appointments WHERE id=:id FOR UPDATE"
            ),
            {"id": appointment_id},
        ).mappings().first()
        if not appointment:
            raise HTTPException(status_code=404, detail="Rendez-vous introuvable.")

        room = db.execute(
            text("SELECT id FROM rooms WHERE id=:id AND active=true"),
            {"id": payload.to_location_id},
        ).mappings().first()
        if not room:
            raise HTTPException(status_code=400, detail="Salle de destination invalide.")

        totals: dict[UUID, Decimal] = defaultdict(Decimal)
        comments: dict[UUID, str | None] = {}
        for consumption in payload.consumptions:
            totals[consumption.stock_unit_id] += consumption.quantity
            comments[consumption.stock_unit_id] = consumption.comment

        checked = []
        for stock_unit_id, quantity in totals.items():
            unit = db.execute(
                text(
                    "SELECT id,quantity,reserved_quantity FROM stock_units "
                    "WHERE id=:id FOR UPDATE"
                ),
                {"id": stock_unit_id},
            ).mappings().first()
            if not unit or unit["quantity"] - unit["reserved_quantity"] < quantity:
                raise HTTPException(
                    status_code=409,
                    detail=f"Stock disponible insuffisant pour {stock_unit_id}.",
                )
            checked.append((stock_unit_id, quantity, comments[stock_unit_id]))

        db.execute(
            text(
                "INSERT INTO patient_flow_history"
                "(appointment_id,from_location_id,to_location_id,transferred_by,handover_comment) "
                "VALUES(:a,:f,:t,:u,:c)"
            ),
            {
                "a": appointment_id,
                "f": appointment["current_location_id"],
                "t": payload.to_location_id,
                "u": user_id,
                "c": payload.handover_comment,
            },
        )
        db.execute(
            text(
                "INSERT INTO appointment_status_history"
                "(appointment_id,old_state,new_state,changed_by,comment) "
                "VALUES(:a,:o,:n,:u,:c)"
            ),
            {
                "a": appointment_id,
                "o": appointment["state"],
                "n": payload.new_state,
                "u": user_id,
                "c": payload.handover_comment,
            },
        )
        db.execute(
            text(
                "UPDATE appointments SET state=CAST(:s AS appointment_state),"
                "current_location_id=:l,updated_at=now(),version=version+1 WHERE id=:a"
            ),
            {"s": payload.new_state, "l": payload.to_location_id, "a": appointment_id},
        )
        if payload.start_now:
            db.execute(
                text(
                    "UPDATE appointments SET ends_at=now()+(ends_at-starts_at),starts_at=now() "
                    "WHERE id=:a"
                ),
                {"a": appointment_id},
            )
        if payload.recovery_comment:
            db.execute(
                text(
                    "INSERT INTO recovery_notes(appointment_id,room_id,comment,created_by) "
                    "VALUES(:a,:r,:c,:u)"
                ),
                {
                    "a": appointment_id,
                    "r": payload.to_location_id,
                    "c": payload.recovery_comment,
                    "u": user_id,
                },
            )
        if payload.discharge_instructions or payload.discharge_notes:
            db.execute(
                text(
                    "INSERT INTO discharge_instructions"
                    "(appointment_id,instructions,notes,created_by) "
                    "VALUES(:a,:i,:n,:u) ON CONFLICT(appointment_id) DO UPDATE "
                    "SET instructions=excluded.instructions,notes=excluded.notes,"
                    "updated_at=now()"
                ),
                {
                    "a": appointment_id,
                    "i": payload.discharge_instructions,
                    "n": payload.discharge_notes,
                    "u": user_id,
                },
            )
        if payload.follow_up.required:
            if payload.follow_up.appointment_type_id is None:
                raise HTTPException(
                    status_code=422,
                    detail="Le type du rendez-vous de suivi est obligatoire.",
                )
            db.execute(
                text(
                    "INSERT INTO follow_up_tasks"
                    "(patient_id,source_appointment_id,appointment_type_id,doctor_id,"
                    "target_date,notes,created_by) "
                    "VALUES(:p,:a,:t,:d,:dt,:n,:u)"
                ),
                {
                    "p": appointment["patient_id"],
                    "a": appointment_id,
                    "t": payload.follow_up.appointment_type_id,
                    "d": payload.follow_up.doctor_id,
                    "dt": payload.follow_up.target_date,
                    "n": payload.follow_up.notes,
                    "u": user_id,
                },
            )

        for stock_unit_id, quantity, comment in checked:
            db.execute(
                text("UPDATE stock_units SET quantity=quantity-:q WHERE id=:id"),
                {"q": quantity, "id": stock_unit_id},
            )
            db.execute(
                text(
                    "INSERT INTO appointment_stock_usage"
                    "(appointment_id,stock_unit_id,used_quantity) VALUES(:a,:s,:q) "
                    "ON CONFLICT(appointment_id,stock_unit_id) DO UPDATE "
                    "SET used_quantity=appointment_stock_usage.used_quantity+excluded.used_quantity"
                ),
                {"a": appointment_id, "s": stock_unit_id, "q": quantity},
            )
            db.execute(
                text(
                    "INSERT INTO stock_movements"
                    "(stock_unit_id,appointment_id,patient_id,kind,quantity,created_by,comment) "
                    "VALUES(:s,:a,:p,'OUT',:q,:u,:c)"
                ),
                {
                    "s": stock_unit_id,
                    "a": appointment_id,
                    "p": appointment["patient_id"],
                    "q": quantity,
                    "u": user_id,
                    "c": comment,
                },
            )
        db.commit()
        return {
            "ok": True,
            "appointment_id": str(appointment_id),
            "state": payload.new_state,
            "location_id": str(payload.to_location_id),
            "stock_lines": len(checked),
        }
    except Exception:
        db.rollback()
        raise

from fastapi import FastAPI

from app.modules.patients.router import router as patients_router
from app.modules.providers.router import router as providers_router
from app.modules.agenda.router import router as agenda_router
from app.modules.encounters.router import router as encounters_router
from app.modules.inami.router import router as inami_router
from app.modules.insurability.router import router as insurability_router
from app.modules.billing.router import router as billing_router
from app.modules.correspondence.router import router as correspondence_router
from app.modules.inventory.router import router as inventory_router
from app.modules.notifications.router import router as notifications_router
from app.modules.integrations.router import router as integrations_router
from app.modules.audit.router import router as audit_router


app = FastAPI(
    title="QR-OCR-App V3",
    version="3.0.0",
)


@app.get("/health", tags=["system"])
def health():
    return {
        "status": "ok",
        "application": "QR-OCR-App",
        "version": "3.0.0",
    }


routers = [
    patients_router,
    providers_router,
    agenda_router,
    encounters_router,
    inami_router,
    insurability_router,
    billing_router,
    correspondence_router,
    inventory_router,
    notifications_router,
    integrations_router,
    audit_router,
]


for router in routers:
    app.include_router(router, prefix="/api/v3")
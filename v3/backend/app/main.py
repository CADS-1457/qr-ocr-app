from fastapi import FastAPI
from app.modules import patients, providers, agenda, encounters, inami, insurability, billing, correspondence, inventory, notifications, integrations, audit
app=FastAPI(title='QR-OCR-App V3', version='3.0.0')
for module in [patients,providers,agenda,encounters,inami,insurability,billing,correspondence,inventory,notifications,integrations,audit]:
    app.include_router(module.router.router, prefix='/api/v3')

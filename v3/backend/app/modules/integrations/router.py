from fastapi import APIRouter
router = APIRouter(prefix="/integrations", tags=["integrations"])

@router.get("/health")
def health():
    return {"module":"integrations","status":"ok"}

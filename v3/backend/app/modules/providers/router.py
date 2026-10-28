from fastapi import APIRouter
router = APIRouter(prefix="/providers", tags=["providers"])

@router.get("/health")
def health():
    return {"module":"providers","status":"ok"}

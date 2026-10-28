from fastapi import APIRouter
router = APIRouter(prefix="/patients", tags=["patients"])

@router.get("/health")
def health():
    return {"module":"patients","status":"ok"}

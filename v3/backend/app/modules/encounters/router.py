from fastapi import APIRouter
router = APIRouter(prefix="/encounters", tags=["encounters"])

@router.get("/health")
def health():
    return {"module":"encounters","status":"ok"}

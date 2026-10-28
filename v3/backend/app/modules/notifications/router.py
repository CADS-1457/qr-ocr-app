from fastapi import APIRouter
router = APIRouter(prefix="/notifications", tags=["notifications"])

@router.get("/health")
def health():
    return {"module":"notifications","status":"ok"}

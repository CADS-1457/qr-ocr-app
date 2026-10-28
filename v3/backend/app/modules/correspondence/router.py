from fastapi import APIRouter
router = APIRouter(prefix="/correspondence", tags=["correspondence"])

@router.get("/health")
def health():
    return {"module":"correspondence","status":"ok"}

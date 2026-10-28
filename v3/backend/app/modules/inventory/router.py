from fastapi import APIRouter
router = APIRouter(prefix="/inventory", tags=["inventory"])

@router.get("/health")
def health():
    return {"module":"inventory","status":"ok"}

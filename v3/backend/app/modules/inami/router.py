from fastapi import APIRouter
router = APIRouter(prefix="/inami", tags=["inami"])

@router.get("/health")
def health():
    return {"module":"inami","status":"ok"}

from fastapi import APIRouter
router = APIRouter(prefix="/audit", tags=["audit"])

@router.get("/health")
def health():
    return {"module":"audit","status":"ok"}

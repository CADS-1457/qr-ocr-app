from fastapi import APIRouter
router = APIRouter(prefix="/agenda", tags=["agenda"])

@router.get("/health")
def health():
    return {"module":"agenda","status":"ok"}

from fastapi import APIRouter
router = APIRouter(prefix="/billing", tags=["billing"])

@router.get("/health")
def health():
    return {"module":"billing","status":"ok"}

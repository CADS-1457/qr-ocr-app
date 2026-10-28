from fastapi import APIRouter
router = APIRouter(prefix="/insurability", tags=["insurability"])

@router.get("/health")
def health():
    return {"module":"insurability","status":"ok"}

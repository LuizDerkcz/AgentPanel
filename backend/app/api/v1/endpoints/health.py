from fastapi import APIRouter


router = APIRouter()


@router.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}

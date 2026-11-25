# Маячок жеста для статистики посещений TlibWebApp
# Описание: POST /api/gesture — пустой приём sendBeacon с первого жеста на странице.
#           Сам endpoint ничего не пишет: TrafficStatsMiddleware по этому пути
#           помечает IP человеком. Ответ в общем конверте API.

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["stats"])


@router.post("/api/gesture")
async def gesture():
    """Принимает маячок жеста. Учёт посетителя делает middleware."""
    return JSONResponse({"success": True})

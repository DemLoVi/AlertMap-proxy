import time
import json
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from pydantic.types import StringConstraints
from typing_extensions import Annotated
from pydantic_settings import BaseSettings, SettingsConfigDict

import httpx
import redis.asyncio as redis

import logging


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("api_proxy")
logging.getLogger("httpx").setLevel(logging.WARNING)


class Settings(BaseSettings):
    api_token: str
    redis_host: str
    redis_port: int
    soft_ttl: int
    lock_ttl: int
    hard_ttl: int

    model_config = SettingsConfigDict(env_file="config/.env")


settings = Settings()


AlertPattern = Annotated[
    str,
    StringConstraints(
        min_length=130,
        max_length=130,
        pattern="^[NAP]+$"
    )
]


class AlertPatternResponse(BaseModel):
    """
    Air raid alert status pattern for all regions.
    """

    pattern: AlertPattern = Field(
        ...,
        description=(
            "A fixed-length string of **130 characters**, where each character "
            "represents the alert status for a specific region:\n\n"
            "- `N` — no alert\n"
            "- `A` — active alert\n"
            "- `P` — partial alert"
        ),
        example="NNNNNNAANNNAPNNPPA" + "N" * 112
    )


app = FastAPI(
    title="alerts.in.ua proxy",
    description=(
        "Proxy API with caching for retrieving air raid alert "
        "status data for regions of Ukraine."
    ),
    version="0.5.0",
    openapi_tags=[
        {
            "name": "Alerts",
            "description": "Air raid alert data"
        }
    ]
)


r = redis.Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    decode_responses=True
)

CACHE_KEY = "api:v1:pattern_list"
LOCK_KEY = "lock:api:v1:pattern_list"


async def get_api_data():
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.alerts.in.ua/v1/iot/active_air_raid_alerts.json",
            params={"token": settings.api_token},
        )
        resp.raise_for_status()
        all_alerts = resp.json()

    ranges = [
        (30, 153),
        (564, 564),
        (1293, 1293),
        (1801, 1804),
    ]

    length = len(all_alerts)

    result = "".join(
        all_alerts[s:e + 1]
        for s, e in ranges
        if s < length
    )

    return result


# --- Lock helpers ---
async def acquire_lock(lock_key: str, ttl: int):
    token = str(uuid.uuid4())
    ok = await r.set(lock_key, token, nx=True, ex=ttl)
    return token if ok else None


async def release_lock(lock_key: str, token: str):
    lua = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """
    await r.eval(lua, 1, lock_key, token)


# --- Cache helpers ---
async def get_cache():
    raw = await r.get(CACHE_KEY)
    return json.loads(raw) if raw else None


async def save_cache(value):
    payload = {
        "value": value,
        "updated_at": int(time.time())
    }
    await r.set(
        CACHE_KEY,
        json.dumps(payload),
        ex=settings.hard_ttl
    )


# --- Endpoint ---
@app.get(
    "/data",
    tags=["Alerts"],
    summary="Get air raid alert status for all regions",
    description=(
        "Returns a fixed-length **130-character string**, where each character "
        "corresponds to a single region.\n\n"
        "**Legend:**\n"
        "- `N` — no alert\n"
        "- `A` — active alert\n"
        "- `P` — partial alert\n\n"
        "The response is cached. If the upstream API is unavailable, "
        "the last cached value may be returned."
    ),
    response_model=AlertPatternResponse,
    responses={
        503: {
            "description": "Upstream API is unavailable and no cached data exists"
        }
    }
)
async def get_data():
    start_time = time.time()
    try:
        now = int(time.time())
        cache = await get_cache()
        data_source = "cache"

        # Avaible actual cache
        data = None
        if cache:
            age = now - cache["updated_at"]
            if age < settings.soft_ttl:
                data = cache["value"]
                logger.info(f"/data -> served from cache, age={age}s")

        # Cache outdated
        if data is None:
            lock_token = await acquire_lock(LOCK_KEY, settings.lock_ttl)
            data_source = "API"

            if lock_token:
                try:
                    # Requesting data
                    data = await get_api_data()
                    await save_cache(data)
                    logger.info("/data -> fetched from API and cached")
                except Exception:
                    # API not responding
                    if cache:
                        data = cache["value"]
                        data_source = "cache (fallback)"
                        logger.warning("/data -> API unavailable, using cached data")
                    else:
                        logger.error("/data -> API unavailable and no cache", exc_info=True)
                        raise HTTPException(status_code=503, detail="API unavailable")
                finally:
                    await release_lock(LOCK_KEY, lock_token)

            # Data updating already
            else:
                if cache:
                    data = cache["value"]
                    data_source = "cache (lock held)"
                    logger.info("/data -> returning cached data while lock held")
                else:
                    logger.error("/data -> Data temporarily unavailable, no cache and lock held")
                    raise HTTPException(status_code=503, detail="Data temporarily unavailable")

        duration = round(time.time() - start_time, 3)
        logger.info(f"/data -> served from {data_source} in {duration}s")
        return {"pattern": data}

    except Exception as e:
        logger.exception("/data -> unhandled exception")
        raise
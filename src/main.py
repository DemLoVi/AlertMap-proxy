import time
import json
import uuid

from fastapi import FastAPI, HTTPException
from pydantic_settings import BaseSettings, SettingsConfigDict

import httpx
import redis.asyncio as redis


class Settings(BaseSettings):
    api_token: str
    redis_host: str
    redis_port: int
    soft_ttl: int
    lock_ttl: int
    hard_ttl: int

    model_config = SettingsConfigDict(env_file="config/.env")


settings = Settings()
app = FastAPI()


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
@app.get("/data")
async def get_data():
    now = int(time.time())
    cache = await get_cache()

    # Avaible actual cache
    if cache:
        age = now - cache["updated_at"]
        if age < settings.soft_ttl:
            return cache["value"]
        
    # Cache outdated
    lock_token = await acquire_lock(LOCK_KEY, settings.lock_ttl)

    if lock_token:
        try:
            # Requesting data
            data = await get_api_data()
            await save_cache(data)
            return data
        except Exception:
            # API not responding
            if cache:
                return cache["value"]
            raise HTTPException(status_code=503, detail="API unavailable")
        finally:
            await release_lock(LOCK_KEY, lock_token)

    # Data updating already
    if cache:
        return cache["value"]

    # Some strange thing
    raise HTTPException(status_code=503, detail="Data temporarily unavailable")
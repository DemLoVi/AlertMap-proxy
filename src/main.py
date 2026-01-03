import time
import json
import uuid
from fastapi import FastAPI, HTTPException
from pydantic_settings import BaseSettings, SettingsConfigDict
import redis
import requests


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


def getAPIdata():
    r = requests.get(
        "https://api.alerts.in.ua/v1/iot/active_air_raid_alerts.json",
        params={"token": settings.api_token},
        timeout=10,
    )
    r.raise_for_status
    all_alerts = r.json()

    ranges = [
        (30, 153),
        (564, 564),
        (1293, 1293),
        (1801, 1804),
    ]

    length = len(all_alerts)

    result = ''.join(
        all_alerts[s:e+1]
        for s, e in ranges
        if s < length
    )

    return result


# --- Lock helpers ---
def acquire_lock(lock_key, ttl):
    token = str(uuid.uuid4())
    ok = r.set(lock_key, token, nx=True, ex=ttl)
    return token if ok else None


def release_lock(lock_key, token):
    lua = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """
    r.eval(lua, 1, lock_key, token)


# --- Cache helpers ---
def get_cache():
    raw = r.get(CACHE_KEY)
    return json.loads(raw) if raw else None


def save_cache(value):
    payload = {
        "value": value,
        "updated_at": int(time.time())
    }
    r.set(CACHE_KEY, json.dumps(payload), ex=settings.hard_ttl)


# --- Endpoint ---
@app.get("/data")
def get_data():
    now = int(time.time())
    cache = get_cache()

    # Avaible actual cache
    if cache:
        age = now - cache["updated_at"]
        if age < settings.soft_ttl:
            return cache["value"]

    # Cache outdated
    lock_token = acquire_lock(LOCK_KEY, settings.lock_ttl)

    if lock_token:
        data = getAPIdata()
        try:
            # Requesting data
            save_cache(data)
            return data
        except Exception:
            # API not responding
            if cache:
                return cache["value"]
            raise HTTPException(status_code=503, detail="API unavailable")
        finally:
            release_lock(LOCK_KEY, lock_token)

    # Data updating already
    if cache:
        return cache["value"]

    # Some strange thing
    raise HTTPException(status_code=503, detail="Data temporarily unavailable")
import time
import json
import uuid
from fastapi import FastAPI, HTTPException
from alerts_in_ua import Client as AlertClient
from pydantic_settings import BaseSettings
import redis

class TimerSettings(BaseSettings):
    soft_ttl: int
    lock_ttl: int
    hard_ttl: int

class DbSettings(BaseSettings):
    redis_host: str
    redis_port: int

class Settings(BaseSettings):
    api_token: str
    db = DbSettings()
    tim = TimerSettings()

    class Config:
        env_file = ".env"

settings = Settings()

app = FastAPI()

alerts = AlertClient(token=settings.api_token)

r = redis.Redis(
    host=settings.db.redis_host,
    port=settings.db.redis_port,
    decode_responses=True
)

CACHE_KEY = "api:v1:pattern_list"
LOCK_KEY = "lock:api:v1:pattern_list"



def getAPIdata():
    all_alerts = alerts.get_air_raid_alert_statuses()

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
    r.set(CACHE_KEY, json.dumps(payload), ex=settings.tim.hard_ttl)


# --- Endpoint ---
@app.get("/data")
def get_data():
    now = int(time.time())
    cache = get_cache()

    # Avaible actual cache
    if cache:
        age = now - cache["updated_at"]
        if age < settings.tim.soft_ttl:
            return cache["value"]

    # Cache outdated
    lock_token = acquire_lock(LOCK_KEY, settings.tim.lock_ttl)

    if lock_token:
        try:
            # Requesting data
            data = getAPIdata()
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
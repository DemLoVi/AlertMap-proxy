# Alerts.in.ua API Proxy

A small internal proxy service for working with the **alerts.in.ua API**.
The main purpose of this service is to **cache API responses and filter unnecessary data** before passing it to clients.

## Purpose

The alerts.in.ua API returns a large dataset containing information for **1600+ regions**.
In practice, only a limited subset of regions is required.

This proxy:

* requests data from the external API,
* filters the response to keep only required regions,
* caches the result in Redis,
* serves cached data to clients.

The goal is to reduce response size, avoid unnecessary external requests, and keep client logic simple.

## Requirements

* Python **3.11+**
* Redis
* alerts.in.ua API token

## Configuration

All configuration is done via environment variables.
An example configuration is provided in `.env.example`.

```env
API_TOKEN=token_here

REDIS_HOST=localhost
REDIS_PORT=6379

SOFT_TTL=60
LOCK_TTL=5
REDIS_HARD_TTL=86400
```

### Configuration notes

* `API_TOKEN` — **required**, token for the alerts.in.ua API
* `SOFT_TTL` — time (in seconds) after which cached data is considered outdated and should be refreshed
* `LOCK_TTL` — Redis lock lifetime to prevent multiple concurrent refreshes
* `REDIS_HARD_TTL` — maximum time data is stored in Redis, even if refresh fails

In most cases, only `API_TOKEN` needs to be changed.

## Installation

1. Install Python 3.11 or newer
2. Install and start Redis
3. Install Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` and set your API token

## Running the Service

Run the server using uvicorn:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

After startup:

* API base URL: `http://localhost:8000`
* Swagger UI: `http://localhost:8000/docs`

## Notes

* The service assumes Redis is always available.
* Region filtering logic is hardcoded and can be adjusted in the source code.
* Intended for internal use, but can be reused in other projects if needed.
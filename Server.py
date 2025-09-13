# Server.py
import os
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="FinTrack Ping Server")

@app.get("/")
async def root():
    return {"ok": True, "app": "FinTrack", "msg": "alive"}

@app.get("/healthz")
async def health():
    return JSONResponse({"status": "ok"})

# Optional simple metrics
@app.get("/metrics")
async def metrics():
    return {"requests_total": 1}

# Run on Render: uvicorn Server:app --host 0.0.0.0 --port $PORT

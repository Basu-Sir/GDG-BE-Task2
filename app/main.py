"""FastAPI application entry-point."""

import logging

from fastapi import FastAPI

from app.routers import notifications, registrations, workshops

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

app = FastAPI(
    title="Workshop Registration API",
    description="Durable workshop registration with transactional-outbox notifications.",
    version="1.0.0",
)

app.include_router(workshops.router)
app.include_router(registrations.router)
app.include_router(notifications.router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok"}

"""Video Production Platform - FastAPI Application Entry Point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.models.init_db import init_db
from app.routers import assets, auth, batches, config, copywriting, forbidden_words, mix, pages, reviews, tasks, tts, users
from app.utils.errors import register_exception_handlers
from app.utils.logging import RequestIdMiddleware, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and logging on startup."""
    setup_logging()
    init_db()
    yield


app = FastAPI(
    title="视频生产平台",
    description="基于 MoneyPrinterTurbo 架构扩展的视频生产平台",
    version="1.0.0",
    lifespan=lifespan,
)

# Register exception handlers
register_exception_handlers(app)

# Request ID middleware (must be added before other middleware)
app.add_middleware(RequestIdMiddleware)

# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/storage", StaticFiles(directory="storage"), name="storage")

# Templates
templates = Jinja2Templates(directory="app/templates")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "video-production-platform"}


# Register routers
app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(config.router)
app.include_router(assets.router)
app.include_router(forbidden_words.router)
app.include_router(copywriting.router)
app.include_router(tts.router)
app.include_router(mix.router)
app.include_router(tasks.router)
app.include_router(batches.router)
app.include_router(reviews.router)

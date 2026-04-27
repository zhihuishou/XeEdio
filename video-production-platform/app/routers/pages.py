"""Page routes - serves HTML templates for the frontend."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Home page - AI 矩阵中枢."""
    return templates.TemplateResponse(request, "home.html", {"request": request})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    return templates.TemplateResponse(request, "login.html", {"request": request})


@router.get("/assets", response_class=HTMLResponse)
async def assets_page(request: Request):
    """Assets library page."""
    return templates.TemplateResponse(request, "assets.html", {"request": request})


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    """Task list page."""
    return templates.TemplateResponse(request, "tasks.html", {"request": request})


@router.get("/tasks/new", response_class=HTMLResponse)
async def new_task_page(request: Request):
    """New task (copywriting generation) page."""
    return templates.TemplateResponse(request, "tasks_new.html", {"request": request})


@router.get("/batches", response_class=HTMLResponse)
async def batches_page(request: Request):
    """Batch tasks page."""
    return templates.TemplateResponse(request, "batches.html", {"request": request})


@router.get("/reviews", response_class=HTMLResponse)
async def reviews_page(request: Request):
    """Review page."""
    return templates.TemplateResponse(request, "reviews.html", {"request": request})


@router.get("/mix", response_class=HTMLResponse)
async def mix_page(request: Request):
    """Smart video mixing — chat-based AI director interface."""
    return templates.TemplateResponse(request, "mix_chat.html", {"request": request})


@router.get("/admin/config", response_class=HTMLResponse)
async def admin_config_page(request: Request):
    """System configuration page (Admin only)."""
    return templates.TemplateResponse(request, "admin_config.html", {"request": request})


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(request: Request):
    """User management page (Admin only)."""
    return templates.TemplateResponse(request, "admin_users.html", {"request": request})


@router.get("/admin/forbidden-words", response_class=HTMLResponse)
async def admin_forbidden_words_page(request: Request):
    """Forbidden words management page (Admin only)."""
    return templates.TemplateResponse(request, "admin_forbidden_words.html", {"request": request})

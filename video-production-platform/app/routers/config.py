"""System configuration router: GET/PUT config endpoints (Admin only)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.database import User, get_db
from app.schemas.config import ConfigItem, ConfigResponse, ConfigUpdateRequest, LLMProvidersResponse
from app.services.config_service import ConfigService
from app.services.external_config import ExternalConfig
from app.utils.auth import require_role

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("", response_model=ConfigResponse)
def get_all_configs(
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_role("admin")),
):
    """Get all system configuration items (Admin only)."""
    service = ConfigService.get_instance()
    all_configs = service.get_all_configs(db)
    configs = {
        key: ConfigItem(**item) for key, item in all_configs.items()
    }
    return ConfigResponse(configs=configs)


@router.put("", response_model=ConfigResponse)
def update_configs(
    body: ConfigUpdateRequest,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_role("admin")),
):
    """Update configuration items (Admin only).

    Accepts key-value pairs. Updates take effect immediately.
    """
    service = ConfigService.get_instance()
    for key, value in body.configs.items():
        service.set_config(key, value, db)

    # Return updated configs
    all_configs = service.get_all_configs(db)
    configs = {
        key: ConfigItem(**item) for key, item in all_configs.items()
    }
    return ConfigResponse(configs=configs)


@router.get("/llm-providers", response_model=LLMProvidersResponse)
def get_llm_providers(
    _current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    """Get available LLM providers from config.yaml.
    
    Returns list of providers with name, model, key_hint.
    API keys are NOT returned for security.
    """
    config = ExternalConfig.get_instance()
    providers = config.get_all_llm_providers()
    # Remove api_key from response for security
    safe_providers = []
    for p in providers:
        safe_providers.append({
            "id": p["id"],
            "name": p["name"],
            "model": p["model"],
            "key_hint": p["key_hint"],
        })
    return LLMProvidersResponse(providers=safe_providers)

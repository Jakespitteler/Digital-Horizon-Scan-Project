from fastapi import APIRouter

from src.api.router_factory import create_crud_router
from src.models.critical_page import CriticalPageServiceModels
from src.models.user import UserServiceModels
from src.models.website import WebsiteServiceModels
from src.services.critical_page_service import CriticalPageService
from src.services.user_service import UserService
from src.services.website_service import WebsiteService

USER_ROUTER: APIRouter = create_crud_router(prefix="/users", service=UserService, service_models=UserServiceModels)
CRITICAL_PAGE_ROUTER: APIRouter = create_crud_router(
    prefix="/critical_pages",
    service=CriticalPageService,
    service_models=CriticalPageServiceModels,
)
WEBSITE_ROUTER: APIRouter = create_crud_router(
    prefix="/websites",
    service=WebsiteService,
    service_models=WebsiteServiceModels,
)

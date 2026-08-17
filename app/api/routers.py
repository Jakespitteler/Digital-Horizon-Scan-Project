from fastapi import APIRouter

from app.api.router_factory import create_crud_router
from app.models.critical_page import CriticalPageServiceModels
from app.models.user import UserServiceModels
from app.models.website import WebsiteServiceModels
from app.services.critical_page_service import CriticalPageService
from app.services.user_service import UserService
from app.services.website_service import WebsiteService

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

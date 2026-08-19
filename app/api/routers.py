from fastapi import APIRouter

from app.api.crud_router_factory import create_crud_router
from app.models import critical_page_models, user_models, website_models
from app.services import critical_page_service, user_service, website_service

USER_ROUTER: APIRouter = create_crud_router(
    prefix="/users",
    service_class=user_service.UserService,
    create_class=user_models.UserCreate,
    update_class=user_models.UserUpdate,
)
CRITICAL_PAGE_ROUTER: APIRouter = create_crud_router(
    prefix="/critical_pages",
    service_class=critical_page_service.CriticalPageService,
    create_class=critical_page_models.CriticalPageCreate,
    update_class=critical_page_models.CriticalPageUpdate,
)
WEBSITE_ROUTER: APIRouter = create_crud_router(
    prefix="/websites",
    service_class=website_service.WebsiteService,
    create_class=website_models.WebsiteCreate,
    update_class=website_models.WebsiteUpdate,
)

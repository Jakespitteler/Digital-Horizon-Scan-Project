from collections.abc import Sequence

from fastapi import APIRouter, status

from app.api.crud_router_factory import create_crud_router
from app.api.dependencies import SessionDep
from app.models import critical_page_models, internal_link_models, website_models
from app.services import critical_page_service, internal_link_service, website_service

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
INTERNAL_LINK_ROUTER: APIRouter = create_crud_router(
    prefix="/internal_links",
    service_class=internal_link_service.InternalLinkService,
    create_class=internal_link_models.InternalLinkCreate,
    update_class=internal_link_models.InternalLinkUpdate,
)


@INTERNAL_LINK_ROUTER.post(
    "/batch",
    response_model=Sequence[internal_link_models.InternalLinkRead],
    status_code=status.HTTP_201_CREATED,
)
def create_item_batch(
    session: SessionDep,
    batch_in: internal_link_models.InternalLinkCreateBatch,
) -> Sequence[internal_link_models.InternalLinkRead]:
    """
    Creates multiple internal links in batch for a single website.
    """
    return internal_link_service.InternalLinkService(session).create_batch(batch_in)

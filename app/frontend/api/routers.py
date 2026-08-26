from collections.abc import Sequence

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.models import critical_page_models, internal_link_models, website_models
from app.db.services import critical_page_service, internal_link_service, website_service
from app.frontend.api.crud_router_factory import create_crud_router
from app.frontend.api.dependencies import SessionDep
from app.scanner import run_website_scanner

ROOT_ROUTER = APIRouter()
templates = Jinja2Templates(directory="app/frontend/templates")


# ======================
# Views
# ======================


@ROOT_ROUTER.get("/", response_model=str)
def get_root(session: SessionDep, request: Request) -> HTMLResponse:
    """
    Root endpoint to check if the server is running.

    Returns:
        A message indicating the server is running.
    """
    all_websites: Sequence[website_models.WebsiteRead] = website_service.WebsiteService(session).get_all()
    context: dict[str, str | list[str]] = {
        "title": "HomePage",
        "heading": "Digital Horizon Scan",
        "message": "Server is Running.",
        "websites": [website.url for website in all_websites],
    }
    return templates.TemplateResponse(request=request, name="index.html", context=context)


@ROOT_ROUTER.post("/scanner", response_class=HTMLResponse)
async def run_scanner(
    session: SessionDep,
    request: Request,
    recipient_email: str = Form(...),
    url: str | None = Form(None),
):
    """Triggers the app from the UI form submission."""
    result_text: str = await run_website_scanner(session, recipient_email, url)

    context: dict[str, str | list[str]] = {"result": result_text}
    # TODO Result is not displayed
    return templates.TemplateResponse(request=request, name="index.html", context=context)


# ======================
# Database Operations
# ======================


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

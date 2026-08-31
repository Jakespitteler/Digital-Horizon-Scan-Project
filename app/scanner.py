import asyncio
import logging
import uuid

from httpx2 import AsyncClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.backend.utils.html import extract_links_from_html, fetch_content_from_url
from app.backend.utils.links import find_link_difference, separate_document_links
from app.backend.web_scraper.errors import TrafficError, WebConnectionError
from app.backend.web_scraper.site_crawler import crawl_site
from app.core.config import config
from app.db.models.critical_page_models import CriticalPageRead, CriticalPageState
from app.db.models.internal_link_models import InternalLinkCreate, InternalLinkRead
from app.db.models.website_models import WebsiteRead, WebsiteState, WebsiteUpdate
from app.db.services.critical_page_service import CriticalPageService
from app.db.services.internal_link_service import InternalLinkService
from app.db.services.website_service import WebsiteService

logger = logging.getLogger(__name__)

MAX_PAGES: int = 5000
BATCH_402_THRESHOLD: int = 20

# ==========================================
# Functions that need to be added


def find_text_differences(previous_state: str, current_state: str) -> tuple[list[str], list[str]]: ...
def format_notification_body(website_state: WebsiteState) -> str: ...
def send_email(email: str, app_password: SecretStr, recipient_email: str, subject: str, body: str) -> None: ...


# ==========================================


# =========
# Scans
# =========


async def scan_critical_page(client: AsyncClient, stored_page: CriticalPageRead) -> CriticalPageState:
    state = CriticalPageState(id=stored_page.id, url=stored_page.url)

    state.updates.text_body, _ = await fetch_content_from_url(client, url=stored_page.url)
    all_links: list[str] = extract_links_from_html(url=state.url, html_content=state.updates.text_body)
    state.updates.documents, state.updates.links = separate_document_links(links=all_links)

    if stored_page.documents:
        state.documents_added, state.documents_removed = find_link_difference(
            previous_state=stored_page.documents,
            current_state=state.updates.documents,
        )
        logger.info(f"{state.documents_added=}")
        logger.info(f"{state.documents_removed=}")
    else:
        stored_page.documents = state.updates.documents

    if stored_page.links:
        state.links_added, state.links_removed = find_link_difference(
            previous_state=stored_page.links,
            current_state=state.updates.links,
        )
        logger.info(f"{state.links_added=}")
        logger.info(f"{state.links_removed=}")
    else:
        state.links_added = state.updates.links

    if stored_page.text_body:
        state.text_added, state.text_removed = find_text_differences(
            previous_state=stored_page.text_body,
            current_state=state.updates.text_body,
        )
        logger.info(f"{state.text_added=}")
        logger.info(f"{state.text_removed=}")
    else:
        state.text_added = [state.updates.text_body]

    return state


async def scan_website(
    client: AsyncClient,
    stored_website: WebsiteRead,
    max_pages: int | None,
    delay: float | None,
    concurrent: int | None,
) -> WebsiteState:
    state = WebsiteState(id=stored_website.id, url=stored_website.url)

    for critical_page in stored_website.critical_pages:
        state.critical_page_states.append(await scan_critical_page(client, critical_page))

    current_internal_links = list(
        await crawl_site(
            client,
            url=stored_website.url,
            delay=delay or stored_website.recommended_delay,
            max_concurrent=concurrent or stored_website.recommended_concurrent,
            max_pages=max_pages or MAX_PAGES,
            batch_403_threshold=BATCH_402_THRESHOLD,
        )
    )

    state.added_internal_links, state.removed_internal_links = find_link_difference(
        previous_state=[link.url for link in stored_website.internal_links],
        current_state=current_internal_links,
    )
    logger.info(f"{state.added_internal_links=}")
    logger.info(f"{state.removed_internal_links=}")
    return state


# =========
# Update DB
# =========


def reduce_crawler_speed_on_website(session: Session, website: WebsiteRead) -> WebsiteRead:
    return WebsiteService(session).update(
        id=website.id,
        model_update=WebsiteUpdate(  # TODO Probably not the best method
            recommended_delay=website.recommended_delay + 0.5,
            recommended_concurrent=website.recommended_concurrent // 2
            if website.recommended_concurrent > 1
            else website.recommended_concurrent,
        ),
    )


def update_database_critical_pages(session: Session, critical_page_states: list[CriticalPageState]) -> None:
    for critical_page in critical_page_states:
        CriticalPageService(session).update(id=critical_page.id, model_update=critical_page.updates)


def update_database_internal_links(
    session: Session,
    website_id: uuid.UUID,
    links_added: list[str] | None,
    links_removed: list[str] | None,
) -> None:
    if links_added:
        for link in links_added:
            InternalLinkService(session).create(model_create=InternalLinkCreate(url=link, website_id=website_id))
    if links_removed:
        for link in links_removed:
            internal_link: InternalLinkRead = InternalLinkService(session).get_by_url(url=link)
            InternalLinkService(session).delete(id=internal_link.id)


# =========
# Main
# =========


async def run_website_scanner(
    client: AsyncClient,
    session: Session,
    website: WebsiteRead,
    recipient_email: str,
    max_pages: int | None = None,
    delay: float | None = None,
    concurrent: int | None = None,
) -> str:
    try:
        website_state: WebsiteState = await scan_website(client, website, max_pages, delay, concurrent)

    except TrafficError as e:  # TODO Make sure we are actually catching all of the errors and reacting accordingly
        logger.error(f"Too many requests for website, waiting and reducing speed then trying again. {e}")
        reduce_crawler_speed_on_website(session, website)
        await asyncio.sleep(1 * 60)  # TODO  maybe test a fetch and wait till open
        return await run_website_scanner(client, session, website, recipient_email, max_pages, delay, concurrent)

    except WebConnectionError as e:
        logger.error(f"Lost connection, waiting and trying again. {e}")
        await asyncio.sleep(1 * 60)  # TODO  maybe test a fetch and wait till open
        return await run_website_scanner(client, session, website, recipient_email, max_pages, delay, concurrent)

    text_body: str = format_notification_body(website_state)

    # TODO If any below fail then we may need to rollback database change and do something else
    update_database_critical_pages(session, website_state.critical_page_states)
    update_database_internal_links(
        session,
        website_id=website_state.id,
        links_added=website_state.added_internal_links,
        links_removed=website_state.removed_internal_links,
    )
    send_email(
        email=config.email,
        app_password=config.email_password,
        recipient_email=recipient_email,
        subject="Website Update",
        body=text_body,
    )
    return text_body

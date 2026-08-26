import asyncio
import logging
import uuid

from httpx2 import AsyncClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.backend.web_scraper.errors import TrafficError, WebConnectionError
from app.backend.web_scraper.site_crawler import crawl_site
from app.backend.web_scraper.utils import extract_links, fetch_content_from_url
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


def find_link_difference(previous_state: list[str], current_state: list[str]) -> tuple[list[str], list[str]]:
    return list(set(current_state) - set(previous_state)), list(set(previous_state) - set(current_state))


def find_text_differences(previous_state: str, current_state: str) -> tuple[list[str], list[str]]: ...
def separate_document_links(links: list[str]) -> tuple[list[str], list[str]]: ...
def format_notification_body(website_state: WebsiteState) -> str: ...
def send_email(email: str, app_password: SecretStr, recipient_email: str, subject: str, body: str) -> None: ...


# ==========================================


async def scan_critical_page(client: AsyncClient, stored_page: CriticalPageRead) -> CriticalPageState:
    state = CriticalPageState(id=stored_page.id, url=stored_page.url)

    state.updates.text_body, _ = await fetch_content_from_url(client, url=stored_page.url)
    all_links: list[str] = extract_links(url=state.url, html_content=state.updates.text_body)
    state.updates.documents, state.updates.links = separate_document_links(links=all_links)

    if stored_page.documents:
        state.documents_added, state.documents_removed = find_link_difference(
            previous_state=stored_page.documents,
            current_state=state.updates.documents,
        )
    else:
        stored_page.documents = state.updates.documents

    if stored_page.links:
        state.links_added, state.links_removed = find_link_difference(
            previous_state=stored_page.links,
            current_state=state.updates.links,
        )
    else:
        state.links_added = state.updates.links

    if stored_page.text_body:
        state.text_added, state.text_removed = find_text_differences(
            previous_state=stored_page.text_body,
            current_state=state.updates.text_body,
        )
    else:
        state.text_added = [state.updates.text_body]

    return state


async def scan_website(session: Session, client: AsyncClient, stored_website: WebsiteRead) -> WebsiteState:
    state = WebsiteState(id=stored_website.id, url=stored_website.url)

    for critical_page in stored_website.critical_pages:
        state.critical_page_states.append(await scan_critical_page(client, critical_page))

    try:
        current_internal_links = list(
            await crawl_site(
                client,
                url=stored_website.url,
                delay=stored_website.recommended_delay,
                max_concurrent=stored_website.recommended_concurrent,
                max_pages=MAX_PAGES,
                batch_403_threshold=BATCH_402_THRESHOLD,
            )
        )

    except TrafficError as e:  # TODO Make sure we are actually catching all of the errors and reacting accordingly
        logger.error(f"Too many requests for website, waiting and reducing speed then trying again. {e}")
        stored_website = WebsiteService(session).update(
            id=stored_website.id,
            model_update=WebsiteUpdate(  # TODO Probably not the best method
                recommended_delay=stored_website.recommended_delay + 0.5,
                recommended_concurrent=stored_website.recommended_concurrent // 2
                if stored_website.recommended_concurrent > 1
                else stored_website.recommended_concurrent,
            ),
        )
        await asyncio.sleep(5 * 60)  # TODO  maybe test a fetch and wait till open
        return await scan_website(session, client, stored_website)

    except WebConnectionError as e:
        logger.error(f"Lost connection, waiting and trying again. {e}")
        await asyncio.sleep(120)  # TODO  maybe test a fetch and wait till open
        return await scan_website(session, client, stored_website)

    state.new_internal_links, state.removed_internal_links = find_link_difference(
        previous_state=[link.url for link in stored_website.internal_links],
        current_state=current_internal_links,
    )
    return state


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


async def run_website_scanner(session: Session, recipient_email: str) -> str:
    async with AsyncClient() as client:
        for website in WebsiteService(session).get_all():
            website_state: WebsiteState = await scan_website(session, client, website)

            text_body: str = format_notification_body(website_state)
            update_database_critical_pages(session, website_state.critical_page_states)
            update_database_internal_links(
                session,
                website_id=website_state.id,
                links_added=website_state.new_internal_links,
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

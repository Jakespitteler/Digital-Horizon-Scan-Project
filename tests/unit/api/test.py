from pydantic import BaseModel, SecretStr


class CriticalPageStateUpdate(BaseModel):
    url: str
    links_added: list[str]
    links_removed: list[str]
    documents_added: list[str]
    documents_removed: list[str]
    text_added: list[str]
    text_removed: list[str]


class WebsiteStateUpdate(BaseModel):
    url: str
    new_pages: list[str]
    removed_pages: list[str]
    critical_page_state_updates: list[CriticalPageStateUpdate]


def format_body(
    website_updates: list[WebsiteStateUpdate],
) -> str: ...  # user can have multiple websites being monitored so website_updates is a list


def send_email(email: str, email_password: SecretStr, recipient_email: str, subject: str, body: str) -> bool: ...

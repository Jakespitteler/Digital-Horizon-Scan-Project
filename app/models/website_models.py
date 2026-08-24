import uuid

from pydantic import BaseModel, ConfigDict

from app.models.critical_page_models import CriticalPageCreateNoWebsite, CriticalPageRead
from app.models.internal_link_models import InternalLinkCreateNoWebsite, InternalLinkRead
from app.utils.field_types import URLString


class WebsiteCreate(BaseModel):
    url: URLString
    critical_pages: list[CriticalPageCreateNoWebsite]
    internal_links: list[InternalLinkCreateNoWebsite]


class WebsiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: URLString
    critical_pages: list[CriticalPageRead]
    internal_links: list[InternalLinkRead]


class WebsiteUpdate(BaseModel):
    url: URLString | None = None

import uuid

from pydantic import BaseModel, ConfigDict

from app.models.critical_page_models import CriticalPageRead
from app.utils.field_types import URLString


class WebsiteCreate(BaseModel):
    url: URLString
    critical_pages: list[CriticalPageRead]
    internal_links: list[URLString]


class WebsiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: URLString
    critical_pages: list[CriticalPageRead]
    internal_links: list[URLString]


class WebsiteUpdate(BaseModel):
    url: URLString | None = None
    critical_pages: list[CriticalPageRead] | None = None
    internal_links: list[URLString] | None = None

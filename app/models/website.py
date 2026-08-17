import uuid

from pydantic import BaseModel, ConfigDict

from app.models.critical_page import CriticalPageRead
from app.models.interfaces import ServiceModels


class WebsiteCreate(BaseModel):
    url: str
    critical_pages: list[CriticalPageRead]
    internal_links: list[str]


class WebsiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: str
    critical_pages: list[CriticalPageRead]
    internal_links: list[str]


class WebsiteUpdate(BaseModel):
    url: str | None = None
    critical_pages: list[CriticalPageRead] | None = None
    internal_links: list[str] | None = None


WebsiteServiceModels = ServiceModels(model_read=WebsiteRead, model_create=WebsiteCreate, model_update=WebsiteUpdate)

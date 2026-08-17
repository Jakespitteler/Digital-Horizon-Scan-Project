from pydantic import BaseModel


class ServiceModels[READ: BaseModel, CREATE: BaseModel, UPDATE: BaseModel](BaseModel):
    model_read: type[READ]
    model_create: type[CREATE]
    model_update: type[UPDATE]

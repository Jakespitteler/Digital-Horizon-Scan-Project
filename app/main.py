from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api import routers
from app.core.config import config
from app.core.logging import setup_logging
from app.db.core import Base, engine
from app.db.errors import IntegrityError, NotFoundError

setup_logging()

Base.metadata.create_all(bind=engine)
app = FastAPI(title=config.app_name)


@app.exception_handler(NotFoundError)
async def not_found_exception_handler(request: Request, exc: NotFoundError):
    """
    Handles NotFoundError exceptions by returning a 404 status.

    Args:
        request: The incoming request.
        exc: The NotFoundError exception.

    Returns:
        A JSONResponse with a 404 status.
    """
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(exc)},
    )


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    """
    Handles IntegrityError exceptions by returning a 400 status.

    Args:
        request: The incoming request.
        exc: The IntegrityError exception.

    Returns:
        A JSONResponse with a 400 status.
    """
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc)},
    )


@app.get("/", response_model=str)
def get_root() -> str:
    """
    Root endpoint to check if the server is running.

    Returns:
        A message indicating the server is running.
    """
    return "Server is Running."


# Register routes
app.include_router(routers.USER_ROUTER)
app.include_router(routers.WEBSITE_ROUTER)
app.include_router(routers.CRITICAL_PAGE_ROUTER)

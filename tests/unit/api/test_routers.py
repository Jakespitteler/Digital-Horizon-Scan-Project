import uuid

from fastapi.testclient import TestClient
from httpx2 import Response

from app.db.core import Base
from tests.conftest import RouterTestConfig


def test_get_all_records(api_client: TestClient, router_test_config: RouterTestConfig) -> None:
    """
    Tests retrieving a list of records from an api router.

    Args:
        api_client: The FastAPI test client.
        router_test_config: The router configuration.
    """
    response: Response = api_client.get(url=router_test_config.prefix)
    assert response.status_code == 200, response.text


def test_get_record(api_client: TestClient, router_test_config: RouterTestConfig, test_api_record: Base) -> None:
    """
    Tests retrieving an existing record by its ID.

    Args:
        api_client: The FastAPI test client.
        router_test_config: The router configuration.
        test_api_record: An existing record.
    """
    response: Response = api_client.get(url=f"{router_test_config.prefix}/{test_api_record.id}")
    assert response.status_code == 200, response.text


def test_get_record_not_found(api_client: TestClient, router_test_config: RouterTestConfig) -> None:
    """
    Tests that retrieving a non-existent record returns a 404 status.

    Args:
        api_client: The FastAPI test client.
        router_test_config: The router configuration.
    """
    invalid_id: uuid.UUID = uuid.uuid4()
    response: Response = api_client.get(url=f"{router_test_config.prefix}/{invalid_id}")
    assert response.status_code == 404, response.text


def test_create_record(api_client: TestClient, router_test_config: RouterTestConfig) -> None:
    """
    Tests creating a new record.

    Args:
        api_client: The FastAPI test client.
        router_test_config: The router configuration.
    """
    response: Response = api_client.post(
        url=router_test_config.prefix,
        json=router_test_config.model_create.model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text


def test_update_record(api_client: TestClient, router_test_config: RouterTestConfig, test_api_record: Base) -> None:
    """
    Tests updating an existing record's details.

    Args:
        api_client: The FastAPI test client.
        router_test_config: The router configuration.
        test_api_record: An existing record.
    """
    response: Response = api_client.patch(
        url=f"{router_test_config.prefix}/{test_api_record.id}",
        json=router_test_config.model_update.model_dump(exclude_unset=True),
    )
    assert response.status_code == 200, response.text


def test_update_record_not_found(api_client: TestClient, router_test_config: RouterTestConfig) -> None:
    """
    Tests that updating a non-existent record returns a 404 status.

    Args:
        api_client: The FastAPI test client.
        router_test_config: The router configuration.
    """
    invalid_id: uuid.UUID = uuid.uuid4()
    response: Response = api_client.patch(
        url=f"{router_test_config.prefix}/{invalid_id}",
        json=router_test_config.model_update.model_dump(),
    )
    assert response.status_code == 404, response.text


def test_delete_record(api_client: TestClient, router_test_config: RouterTestConfig, test_api_record: Base) -> None:
    """
    Tests deleting an existing record.

    Args:
        api_client: The FastAPI test client.
        router_test_config: The router configuration.
        test_api_record: An existing record.
    """
    response: Response = api_client.delete(url=f"{router_test_config.prefix}/{test_api_record.id}")
    assert response.status_code == 204, response.text

    # Assert it can no longer be retrieved
    fetch_response: Response = api_client.get(url=f"{router_test_config.prefix}/{test_api_record.id}")
    assert fetch_response.status_code == 404, response.text


def test_delete_record_not_found(api_client: TestClient, router_test_config: RouterTestConfig) -> None:
    """
    Tests that deleting a non-existent record returns a 404 status.

    Args:
        api_client: The FastAPI test client.
        router_test_config: The router configuration.
    """
    invalid_id: uuid.UUID = uuid.uuid4()
    response: Response = api_client.delete(url=f"{router_test_config.prefix}/{invalid_id}")
    assert response.status_code == 404, response.text

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    """A test client for the FastAPI app, hitting real routes end-to-end."""
    return TestClient(app)

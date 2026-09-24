import pytest

from app.core.config import Settings

# Ticket #128: `repr(settings)` ends up in exception messages (e.g. pytest's
# monkeypatch error for a missing attribute), log lines and debuggers, so it
# must never carry a credential (ENGINEERING-STANDARDS.md §5).
_DUMMY_SECRETS = {
    "database_url": "postgresql+psycopg://dummy_user:dummy-db-password@db.example:5432/dummy_db",
    "media_token_secret_key": "dummy-media-token-secret",
    "aws_access_key_id": "DUMMYACCESSKEYID",
    "aws_secret_access_key": "dummy-aws-secret-access-key",
}


def _settings_with_dummy_secrets() -> Settings:
    # `_env_file=None` so a developer's real `.env` can never be read into
    # this test (or its failure output) — every secret field is explicit.
    return Settings(_env_file=None, **_DUMMY_SECRETS)


def test_settings_repr_and_str_never_show_a_secret():
    settings = _settings_with_dummy_secrets()

    for rendered in (repr(settings), str(settings)):
        for field, value in _DUMMY_SECRETS.items():
            assert value not in rendered, f"{field} leaked into {rendered!r}"
        assert "dummy-db-password" not in rendered


def test_monkeypatching_a_missing_setting_never_shows_a_secret(monkeypatch):
    # The exact way ticket #121 surfaced this: pytest's monkeypatch.setattr
    # on a not-yet-existing setting raises an AttributeError whose message
    # embeds `repr()` of the Settings object.
    settings = _settings_with_dummy_secrets()

    with pytest.raises(AttributeError) as excinfo:
        monkeypatch.setattr(settings, "not_a_real_setting", 1)

    for value in _DUMMY_SECRETS.values():
        assert value not in str(excinfo.value)

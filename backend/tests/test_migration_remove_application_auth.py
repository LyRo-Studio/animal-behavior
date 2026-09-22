from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from alembic import command
from alembic.config import Config

# Ticket #72's migration-safety test (same spirit as ticket #60's
# test_db_session_isolation.py): the migration that retires the account
# system must carry each existing analysis's "run by" attribution across
# (user story 8) rather than null it out, and must leave no auth tables or
# enum types behind (user story 7).
#
# Runs against a throwaway database created on the same Postgres server as
# the rest of the suite, migrated only as far as revision 0005 first — the
# shared test schema (already at head, see conftest.py) can't be used since
# the accounts table no longer exists there. `scratch_database` is a shared
# fixture in conftest.py (ticket #82 reuses it for its own migration test).

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _seed_accounts_and_jobs(scratch_url: str) -> dict[str, int]:
    """Insert two accounts (one deactivated) and three analysis jobs across
    them, plus refresh/action tokens hanging off an account — the shapes
    the migration has to drop without tripping over a foreign key."""
    engine = create_engine(scratch_url)
    try:
        with engine.begin() as conn:
            ids = {}
            for email, is_active in [
                ("jan.peeters@vives.be", True),
                ("piet.jansen@vives.be", False),
            ]:
                ids[email] = conn.execute(
                    text(
                        "INSERT INTO accounts "
                        "(email, password_hash, display_name, role, is_active) "
                        "VALUES (:email, 'x', 'Test', 'user', :is_active) RETURNING id"
                    ),
                    {"email": email, "is_active": is_active},
                ).scalar_one()
            for test_id, email in [
                ("T001", "jan.peeters@vives.be"),
                ("T002", "jan.peeters@vives.be"),
                ("T003", "piet.jansen@vives.be"),
            ]:
                conn.execute(
                    text("INSERT INTO analysis_jobs (test_id, requested_by) VALUES (:t, :a)"),
                    {"t": test_id, "a": ids[email]},
                )
            account_id = ids["jan.peeters@vives.be"]
            conn.execute(
                text(
                    "INSERT INTO refresh_tokens (account_id, token_hash, expires_at) "
                    "VALUES (:a, 'hash-1', now())"
                ),
                {"a": account_id},
            )
            conn.execute(
                text(
                    "INSERT INTO account_action_tokens "
                    "(account_id, purpose, token_hash, expires_at) "
                    "VALUES (:a, 'invite', 'hash-2', now())"
                ),
                {"a": account_id},
            )
            return ids
    finally:
        engine.dispose()


def test_upgrade_backfills_requested_by_identity_from_the_old_account_email(scratch_database):
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "0005")
    _seed_accounts_and_jobs(scratch_database)

    command.upgrade(config, "0006")

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT test_id, requested_by_identity FROM analysis_jobs ORDER BY test_id")
            ).all()
    finally:
        engine.dispose()
    assert [tuple(row) for row in rows] == [
        ("T001", "jan.peeters@vives.be"),
        ("T002", "jan.peeters@vives.be"),
        # A deactivated account's jobs keep their attribution too.
        ("T003", "piet.jansen@vives.be"),
    ]


def test_upgrade_drops_every_auth_table_column_and_enum_type(scratch_database):
    config = Config(str(_ALEMBIC_INI))
    command.upgrade(config, "0005")
    _seed_accounts_and_jobs(scratch_database)

    command.upgrade(config, "0006")

    engine = create_engine(scratch_database)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        job_columns = {column["name"] for column in inspector.get_columns("analysis_jobs")}
        with engine.connect() as conn:
            enum_types = set(
                conn.execute(text("SELECT typname FROM pg_type WHERE typtype = 'e'")).scalars()
            )
    finally:
        engine.dispose()

    assert not tables & {"accounts", "refresh_tokens", "account_action_tokens"}
    assert "requested_by" not in job_columns
    assert "requested_by_identity" in job_columns
    assert not enum_types & {"account_role", "account_action_token_purpose"}
    # Unrelated enums are left alone.
    assert {"analysis_job_status", "analysis_job_video_status"} <= enum_types

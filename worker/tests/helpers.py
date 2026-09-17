from app.models.account import Account, AccountRole


def create_account(
    db_session, *, email: str = "jan.peeters@vives.be", display_name: str = "Jan"
) -> Account:
    """A minimal Account for tests that just need *an* owner for an
    AnalysisJob — shared by every worker/tests module (test_orchestrator.py,
    test_startup_recovery.py, ...) so a future required field on `Account`
    only needs updating here, not in every test file that constructs one.
    """
    account = Account(
        email=email,
        password_hash=None,
        display_name=display_name,
        role=AccountRole.USER,
        is_active=True,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account

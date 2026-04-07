import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_get_or_create_user_creates_new(db_session):
    from app.services.credits import CreditService
    svc = CreditService(db_session)
    user = await svc.get_or_create_user("new@test.com", name="New User")
    assert user.email == "new@test.com"
    assert user.name == "New User"
    assert user.id is not None


@pytest.mark.asyncio
async def test_get_or_create_user_returns_existing(db_session):
    from app.services.credits import CreditService
    from app.models import User
    db_session.add(User(email="existing@test.com", name="Old Name"))
    await db_session.commit()
    svc = CreditService(db_session)
    user = await svc.get_or_create_user("existing@test.com", name="New Name")
    assert user.name == "Old Name"


@pytest.mark.asyncio
async def test_grant_credits(db_session):
    from app.services.credits import CreditService
    svc = CreditService(db_session)
    user = await svc.get_or_create_user("grant@test.com")
    await svc.grant_credits(user.id, "video_8s", 5)
    balance = await svc.get_balance(user.email)
    assert balance["video_8s"] == 5


@pytest.mark.asyncio
async def test_grant_credits_adds_to_existing(db_session):
    from app.services.credits import CreditService
    svc = CreditService(db_session)
    user = await svc.get_or_create_user("add@test.com")
    await svc.grant_credits(user.id, "image", 5)
    await svc.grant_credits(user.id, "image", 10)
    balance = await svc.get_balance(user.email)
    assert balance["image"] == 15


@pytest.mark.asyncio
async def test_deduct_credit_success(db_session):
    from app.services.credits import CreditService
    svc = CreditService(db_session)
    user = await svc.get_or_create_user("deduct@test.com")
    await svc.grant_credits(user.id, "video_8s", 5)
    ok = await svc.deduct_credit(user.id, "video_8s")
    assert ok is True
    balance = await svc.get_balance(user.email)
    assert balance["video_8s"] == 4


@pytest.mark.asyncio
async def test_deduct_credit_fails_when_zero(db_session):
    from app.services.credits import CreditService
    svc = CreditService(db_session)
    user = await svc.get_or_create_user("zero@test.com")
    ok = await svc.deduct_credit(user.id, "video_8s")
    assert ok is False


@pytest.mark.asyncio
async def test_get_balance_multiple_services(db_session):
    from app.services.credits import CreditService
    svc = CreditService(db_session)
    user = await svc.get_or_create_user("multi@test.com")
    await svc.grant_credits(user.id, "video_8s", 2)
    await svc.grant_credits(user.id, "video_15s", 3)
    await svc.grant_credits(user.id, "video_22s", 1)
    await svc.grant_credits(user.id, "video_30s", 1)
    await svc.grant_credits(user.id, "image", 10)
    await svc.grant_credits(user.id, "landing_page", 5)
    balance = await svc.get_balance(user.email)
    assert balance == {
        "video_8s": 2, "video_15s": 3, "video_22s": 1, "video_30s": 1,
        "image": 10, "landing_page": 5,
    }

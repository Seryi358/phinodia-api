import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_create_user(db_session):
    from app.models import User
    user = User(email="test@example.com", name="Test User", data_consent=True)
    db_session.add(user)
    await db_session.commit()

    result = await db_session.execute(select(User).where(User.email == "test@example.com"))
    found = result.scalar_one()
    assert found.name == "Test User"
    assert found.data_consent is True


@pytest.mark.asyncio
async def test_create_credit(db_session):
    from app.models import User, Credit
    user = User(email="credit@test.com")
    db_session.add(user)
    await db_session.commit()

    credit = Credit(user_id=user.id, service_type="video_15s", total=5, used=0)
    db_session.add(credit)
    await db_session.commit()

    result = await db_session.execute(select(Credit).where(Credit.user_id == user.id))
    found = result.scalar_one()
    assert found.total == 5
    assert found.remaining == 5


@pytest.mark.asyncio
async def test_create_job(db_session):
    from app.models import User, Job
    user = User(email="job@test.com")
    db_session.add(user)
    await db_session.commit()

    job = Job(
        user_id=user.id,
        service_type="video_15s",
        input_description="Test product",
        input_format="portrait",
    )
    db_session.add(job)
    await db_session.commit()

    assert job.id is not None
    assert job.status == "pending"


@pytest.mark.asyncio
async def test_create_transaction(db_session):
    from app.models import Transaction
    tx = Transaction(
        user_email="pay@test.com",
        wompi_reference="REF123",
        wompi_status="APPROVED",
        amount_cents=3999000,
        package_type="video_15s_5",
        credits_granted=5,
        service_type="video_15s",
    )
    db_session.add(tx)
    await db_session.commit()
    assert tx.id is not None


@pytest.mark.asyncio
async def test_user_email_unique(db_session):
    from app.models import User
    from sqlalchemy.exc import IntegrityError
    db_session.add(User(email="dup@test.com"))
    await db_session.commit()
    db_session.add(User(email="dup@test.com"))
    with pytest.raises(IntegrityError):
        await db_session.commit()

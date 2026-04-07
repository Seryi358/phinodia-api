import hashlib
import pytest


@pytest.mark.asyncio
async def test_webhook_approved_grants_credits(test_client, db_session):
    amount = 2499000  # video_8s_5 package
    secret = "test_events_secret"
    timestamp = 1700000000
    concat = f"tx-001APPROVED{amount}{timestamp}{secret}"
    checksum = hashlib.sha256(concat.encode()).hexdigest()
    event = {
        "event": "transaction.updated",
        "data": {"transaction": {
            "id": "tx-001", "status": "APPROVED", "amount_in_cents": amount,
            "reference": "REF001", "customer_email": "buyer@test.com", "currency": "COP",
        }},
        "signature": {"properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"], "checksum": checksum},
        "timestamp": timestamp,
    }
    async with test_client as client:
        resp = await client.post("/api/v1/payments/webhook", json=event)
        assert resp.status_code == 200
        resp = await client.get("/api/v1/credits/check?email=buyer@test.com")
    assert resp.json()["video_8s"] == 5


@pytest.mark.asyncio
async def test_webhook_invalid_signature_rejected(test_client):
    event = {
        "event": "transaction.updated",
        "data": {"transaction": {"id": "tx-002", "status": "APPROVED", "amount_in_cents": 2499000, "reference": "REF002", "customer_email": "buyer@test.com"}},
        "signature": {"properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"], "checksum": "invalid_checksum"},
        "timestamp": 1700000000,
    }
    async with test_client as client:
        resp = await client.post("/api/v1/payments/webhook", json=event)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_webhook_declined_ignored(test_client):
    secret = "test_events_secret"
    timestamp = 1700000000
    concat = f"tx-003DECLINED24990001700000000{secret}"
    checksum = hashlib.sha256(concat.encode()).hexdigest()
    event = {
        "event": "transaction.updated",
        "data": {"transaction": {"id": "tx-003", "status": "DECLINED", "amount_in_cents": 2499000, "reference": "REF003", "customer_email": "buyer@test.com"}},
        "signature": {"properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"], "checksum": checksum},
        "timestamp": timestamp,
    }
    async with test_client as client:
        resp = await client.post("/api/v1/payments/webhook", json=event)
        assert resp.status_code == 200
        resp = await client.get("/api/v1/credits/check?email=buyer@test.com")
    assert resp.json()["video_8s"] == 0

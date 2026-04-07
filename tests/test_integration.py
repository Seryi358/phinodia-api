import hashlib
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_full_video_flow(db_engine):
    """End-to-end: payment webhook -> credits -> generate video -> check status."""
    from app.main import app
    from app.database import get_db_session
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db
    transport = ASGITransport(app=app)

    # Step 1: Simulate Wompi payment webhook for video_8s_5 package (2499000 COP)
    amount = 2499000
    secret = "test_events_secret"
    timestamp = 1700000000
    concat = f"tx-int-001APPROVED{amount}{timestamp}{secret}"
    checksum = hashlib.sha256(concat.encode()).hexdigest()

    webhook_event = {
        "event": "transaction.updated",
        "data": {"transaction": {
            "id": "tx-int-001", "status": "APPROVED", "amount_in_cents": amount,
            "reference": "REF-INT-001", "customer_email": "integration@test.com",
            "currency": "COP", "payment_method_type": "PSE",
        }},
        "signature": {
            "properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"],
            "checksum": checksum,
        },
        "timestamp": timestamp,
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Payment webhook
        resp = await client.post("/api/v1/payments/webhook", json=webhook_event)
        assert resp.status_code == 200

        # Step 2: Verify credits
        resp = await client.get("/api/v1/credits/check?email=integration@test.com")
        assert resp.json()["video_8s"] == 5

    # Step 3: Generate video — patch external APIs and run background task inline
    with patch("app.routers.generate.ScriptGenerator") as MockScript, \
         patch("app.routers.generate.KieAIClient") as MockKie, \
         patch("app.routers.generate.asyncio") as mock_asyncio, \
         patch("app.database.SessionLocal", new=session_factory):

        mock_script = AsyncMock()
        MockScript.return_value = mock_script
        mock_script.analyze_product.return_value = "50ml glass jar, premium cream..."
        mock_script.generate_buyer_persona.return_value = "Sofia, 28, Bogota..."
        mock_script.generate_image_prompt.return_value = "Product in UGC context..."
        mock_script.generate_video_prompt.return_value = "A woman holds cream jar in kitchen..."

        mock_kie = AsyncMock()
        MockKie.return_value = mock_kie
        mock_kie.create_image_task.return_value = "task_img_ff"
        mock_kie.get_task_status.return_value = {
            "state": "success", "progress": 100,
            "result_urls": ["https://cdn.kie.ai/first-frame.jpg"],
        }
        mock_kie.create_video_task.return_value = "task_int_123"

        # Capture and immediately run the background task so job is updated in test DB
        captured_coros = []

        def capture_create_task(coro):
            captured_coros.append(coro)
            return MagicMock()

        mock_asyncio.create_task.side_effect = capture_create_task

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/generate/video", json={
                "email": "integration@test.com",
                "image_url": "https://example.com/cream.jpg",
                "description": "Hydrating face cream with vitamin C",
                "format": "portrait",
                "duration": 8,
                "product_name": "Glow Cream",
                "data_consent": True,
            })

        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # Run the background task now so kie_task_id gets written to the test DB
        for coro in captured_coros:
            await coro

    # Step 4: Credits should be deducted
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/credits/check?email=integration@test.com")
        assert resp.json()["video_8s"] == 4

    # Step 5: Check job status (mock VEO poll)
    with patch("app.routers.jobs.KieAIClient") as MockKie:
        mock_kie = AsyncMock()
        MockKie.return_value = mock_kie
        mock_kie.get_video_status.return_value = {
            "state": "success", "progress": 100,
            "result_urls": ["https://cdn.kie.ai/result-int.mp4"],
        }

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/jobs/status/{job_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["result_url"] == "https://cdn.kie.ai/result-int.mp4"
    assert data["result_type"] == "mp4"

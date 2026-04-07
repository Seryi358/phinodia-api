import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_generate_video_success(test_client):
    with patch("app.routers.generate.ScriptGenerator") as MockScript, \
         patch("app.routers.generate.KieAIClient") as MockKie, \
         patch("app.routers.generate.CreditService") as MockCredit:

        mock_credit_instance = AsyncMock()
        MockCredit.return_value = mock_credit_instance
        mock_user = AsyncMock()
        mock_user.id = 1
        mock_user.email = "test@test.com"
        mock_credit_instance.get_or_create_user.return_value = mock_user
        mock_credit_instance.deduct_credit.return_value = True

        mock_script_instance = AsyncMock()
        MockScript.return_value = mock_script_instance
        mock_script_instance.generate_video_prompt.return_value = "A woman holds cream..."

        mock_kie_instance = AsyncMock()
        MockKie.return_value = mock_kie_instance
        mock_kie_instance.create_video_task.return_value = "task_123"

        async with test_client as client:
            resp = await client.post("/api/v1/generate/video", json={
                "email": "test@test.com",
                "image_url": "https://example.com/product.jpg",
                "description": "Face cream with vitamin C",
                "format": "portrait",
                "duration": 8,
                "product_name": "Glow Cream",
                "data_consent": True,
            })

        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "processing"


@pytest.mark.asyncio
async def test_generate_video_no_credits(test_client):
    with patch("app.routers.generate.ScriptGenerator"), \
         patch("app.routers.generate.KieAIClient"), \
         patch("app.routers.generate.CreditService") as MockCredit:

        mock_credit_instance = AsyncMock()
        MockCredit.return_value = mock_credit_instance
        mock_user = AsyncMock()
        mock_user.id = 1
        mock_credit_instance.get_or_create_user.return_value = mock_user
        mock_credit_instance.deduct_credit.return_value = False

        async with test_client as client:
            resp = await client.post("/api/v1/generate/video", json={
                "email": "test@test.com",
                "image_url": "https://example.com/product.jpg",
                "description": "Face cream",
                "format": "portrait",
                "duration": 8,
                "product_name": "Cream",
                "data_consent": True,
            })

        assert resp.status_code == 402
        assert "credits" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_generate_video_no_consent(test_client):
    async with test_client as client:
        resp = await client.post("/api/v1/generate/video", json={
            "email": "test@test.com",
            "image_url": "https://example.com/product.jpg",
            "description": "Face cream",
            "format": "portrait",
            "duration": 8,
            "product_name": "Cream",
            "data_consent": False,
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_generate_video_invalid_duration(test_client):
    async with test_client as client:
        resp = await client.post("/api/v1/generate/video", json={
            "email": "test@test.com",
            "image_url": "https://example.com/product.jpg",
            "description": "Face cream",
            "format": "portrait",
            "duration": 25,
            "product_name": "Cream",
            "data_consent": True,
        })
    assert resp.status_code == 400
    assert "8, 15, 22, or 30" in resp.json()["detail"]

import pytest
from unittest.mock import AsyncMock, patch
from app.models import User, Job


@pytest.mark.asyncio
async def test_get_job_status_pending(test_client, db_session):
    user = User(email="job@test.com")
    db_session.add(user)
    await db_session.commit()
    job = Job(user_id=user.id, service_type="video_8s", status="pending")
    db_session.add(job)
    await db_session.commit()
    async with test_client as client:
        resp = await client.get(f"/api/v1/jobs/status/{job.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    assert resp.json()["progress"] == 0


@pytest.mark.asyncio
async def test_get_job_status_completed(test_client, db_session):
    user = User(email="done@test.com")
    db_session.add(user)
    await db_session.commit()
    job = Job(user_id=user.id, service_type="video_15s", status="completed",
              result_url="https://cdn.kie.ai/result.mp4", result_type="mp4")
    db_session.add(job)
    await db_session.commit()
    async with test_client as client:
        resp = await client.get(f"/api/v1/jobs/status/{job.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["result_url"] == "https://cdn.kie.ai/result.mp4"


@pytest.mark.asyncio
async def test_get_job_status_not_found(test_client):
    async with test_client as client:
        resp = await client.get("/api/v1/jobs/status/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_job_status_generating_polls_veo(test_client, db_session):
    """Video jobs use get_video_status (VEO 3.1 polling)."""
    user = User(email="poll@test.com")
    db_session.add(user)
    await db_session.commit()
    job = Job(user_id=user.id, service_type="video_8s", status="generating", kie_task_id="task_veo_123")
    db_session.add(job)
    await db_session.commit()
    with patch("app.routers.jobs.KieAIClient") as MockKie:
        mock_kie = AsyncMock()
        MockKie.return_value = mock_kie
        mock_kie.get_video_status.return_value = {
            "state": "success", "progress": 100,
            "result_urls": ["https://cdn.kie.ai/video.mp4"],
        }
        async with test_client as client:
            resp = await client.get(f"/api/v1/jobs/status/{job.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["result_url"] == "https://cdn.kie.ai/video.mp4"
    # Verify get_video_status was called (not get_task_status)
    mock_kie.get_video_status.assert_called_once_with("task_veo_123")
    mock_kie.get_task_status.assert_not_called()


@pytest.mark.asyncio
async def test_get_job_status_image_polls_generic(test_client, db_session):
    """Image jobs use get_task_status (generic KIE polling)."""
    user = User(email="imgpoll@test.com")
    db_session.add(user)
    await db_session.commit()
    job = Job(user_id=user.id, service_type="image", status="generating", kie_task_id="task_img_789")
    db_session.add(job)
    await db_session.commit()
    with patch("app.routers.jobs.KieAIClient") as MockKie:
        mock_kie = AsyncMock()
        MockKie.return_value = mock_kie
        mock_kie.get_task_status.return_value = {
            "state": "success", "progress": 100,
            "result_urls": ["https://cdn.kie.ai/image.jpg"],
        }
        async with test_client as client:
            resp = await client.get(f"/api/v1/jobs/status/{job.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["result_url"] == "https://cdn.kie.ai/image.jpg"
    # Verify get_task_status was called (not get_video_status)
    mock_kie.get_task_status.assert_called_once_with("task_img_789")
    mock_kie.get_video_status.assert_not_called()

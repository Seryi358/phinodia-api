from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from starlette.requests import Request


def _request(method: str = "GET") -> Request:
    return Request({"type": "http", "method": method, "path": "/", "headers": []})


@pytest.mark.asyncio
async def test_job_status_includes_download_url_for_completed_video():
    from app.routers.jobs import get_job_status

    job = {
        "id": "11111111-1111-1111-1111-111111111111",
        "status": "completed",
        "service_type": "video_8s",
        "result_type": "mp4",
        "result_url": "https://app.phinodia.com/uploads/results/11111111-1111-1111-1111-111111111111.mp4",
        "error_message": None,
    }
    with patch("app.routers.jobs.db.select_one", AsyncMock(return_value=job)):
        body = await get_job_status(UUID(job["id"]), _request())
    assert body.download_url == f"/api/v1/jobs/download/{job['id']}"
    assert body.result_url.endswith(".mp4")


@pytest.mark.asyncio
async def test_download_endpoint_serves_video_attachment():
    from app.routers.jobs import download_job_result

    job_id = "22222222-2222-2222-2222-222222222222"
    out_dir = Path("/home/sergio/repos-seryi358/phinodia-api/data/uploads/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    file_path = out_dir / f"{job_id}.mp4"
    file_path.write_bytes(b"fake-mp4-data")

    job = {
        "id": job_id,
        "status": "completed",
        "service_type": "video_8s",
        "result_type": "mp4",
        "result_url": f"https://app.phinodia.com/uploads/results/{job_id}.mp4",
    }

    try:
        with patch("app.routers.jobs.db.select_one", AsyncMock(return_value=job)):
            response = await download_job_result(UUID(job_id))
        assert response.media_type.startswith("video/mp4")
        assert "attachment" in response.headers["content-disposition"].lower()
        assert Path(response.path).resolve() == file_path.resolve()
    finally:
        file_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_list_jobs_by_email_includes_download_url():
    from app.routers.jobs import list_jobs_by_email

    user = {"id": "user-1", "email": "test@example.com"}
    jobs = [{
        "id": "33333333-3333-3333-3333-333333333333",
        "service_type": "image",
        "status": "completed",
        "result_type": "jpg",
        "result_url": "https://app.phinodia.com/uploads/results/33333333-3333-3333-3333-333333333333.jpg",
        "input_image_url": "https://app.phinodia.com/uploads/source.jpg",
        "input_description": "Imagen",
        "created_at": "2026-04-28T01:00:00+00:00",
        "completed_at": "2026-04-28T01:01:00+00:00",
        "error_message": None,
    }]
    with patch("app.routers.jobs.db.select_one", AsyncMock(return_value=user)), patch(
        "app.routers.jobs.db.select", AsyncMock(return_value=jobs)
    ):
        body = await list_jobs_by_email("test@example.com")
    assert body[0].download_url == f"/api/v1/jobs/download/{jobs[0]['id']}"


@pytest.mark.asyncio
async def test_job_status_skips_kie_autocomplete_for_multistep_video():
    from app.routers.jobs import get_job_status

    job = {
        "id": "44444444-4444-4444-4444-444444444444",
        "status": "generating",
        "service_type": "video_15s",
        "kie_task_id": "kie-task-1",
        "result_type": "mp4",
        "result_url": None,
        "error_message": None,
        "created_at": "2026-04-28T10:00:00+00:00",
    }
    mock_kie = AsyncMock()

    with patch("app.routers.jobs.db.select_one", AsyncMock(return_value=job)), patch(
        "app.routers.jobs.KieAIClient", return_value=mock_kie
    ), patch(
        "app.routers.jobs.datetime"
    ) as mock_datetime:
        mock_datetime.now.return_value = datetime(2026, 4, 28, 10, 30, tzinfo=timezone.utc)
        mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
        body = await get_job_status(UUID(job["id"]), _request())

    assert body.status == "generating"
    assert mock_kie.get_video_status.await_count == 0


@pytest.mark.asyncio
async def test_job_status_auto_fails_stale_multistep_partial_and_refunds():
    from app.routers.jobs import get_job_status

    job = {
        "id": "55555555-5555-5555-5555-555555555555",
        "status": "generating",
        "service_type": "video_15s",
        "kie_task_id": "kie-task-2",
        "result_type": "mp4",
        "result_url": "https://app.phinodia.com/uploads/results/55555555-5555-5555-5555-555555555555.mp4",
        "error_message": None,
        "created_at": "2026-04-28T00:00:00+00:00",
        "user_id": "user-1",
    }
    mock_credit = AsyncMock()
    mock_credit.refund_credit = AsyncMock(return_value=True)

    with patch("app.routers.jobs.db.select_one", AsyncMock(return_value=job)), patch(
        "app.routers.jobs.db.update",
        AsyncMock(return_value=[{"status": "failed"}]),
    ), patch(
        "app.services.credits.CreditService", return_value=mock_credit
    ), patch(
        "app.routers.jobs.is_video_duration_sufficient", AsyncMock(return_value=False)
    ), patch(
        "app.routers.jobs.datetime"
    ) as mock_datetime:
        mock_datetime.now.return_value = datetime(2026, 4, 28, 2, 0, tzinfo=timezone.utc)
        mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
        body = await get_job_status(UUID(job["id"]), _request())

    assert body.status == "failed"
    assert "credito fue restaurado" in body.error_message.lower()
    mock_credit.refund_credit.assert_awaited_once_with("user-1", "video_15s")


@pytest.mark.asyncio
async def test_job_status_auto_promotes_stale_multistep_when_duration_valid():
    from app.routers.jobs import get_job_status

    job = {
        "id": "66666666-6666-6666-6666-666666666666",
        "status": "generating",
        "service_type": "video_15s",
        "kie_task_id": "kie-task-3",
        "result_type": "mp4",
        "result_url": "https://app.phinodia.com/uploads/results/66666666-6666-6666-6666-666666666666.mp4",
        "error_message": None,
        "created_at": "2026-04-28T00:00:00+00:00",
        "user_id": "user-1",
    }

    with patch("app.routers.jobs.db.select_one", AsyncMock(return_value=job)), patch(
        "app.routers.jobs.db.update",
        AsyncMock(return_value=[{"status": "completed"}]),
    ), patch(
        "app.routers.jobs.is_video_duration_sufficient", AsyncMock(return_value=True)
    ), patch(
        "app.routers.jobs.datetime"
    ) as mock_datetime:
        mock_datetime.now.return_value = datetime(2026, 4, 28, 2, 0, tzinfo=timezone.utc)
        mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
        body = await get_job_status(UUID(job["id"]), _request())

    assert body.status == "completed"
    assert body.result_url.endswith(".mp4")

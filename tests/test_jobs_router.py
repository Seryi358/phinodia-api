from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def test_job_status_includes_download_url_for_completed_video(client):
    job = {
        "id": "11111111-1111-1111-1111-111111111111",
        "status": "completed",
        "service_type": "video_8s",
        "result_type": "mp4",
        "result_url": "https://app.phinodia.com/uploads/results/11111111-1111-1111-1111-111111111111.mp4",
        "error_message": None,
    }
    with patch("app.routers.jobs.db.select_one", AsyncMock(return_value=job)):
        r = client.get(f"/api/v1/jobs/status/{job['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["download_url"] == f"/api/v1/jobs/download/{job['id']}"
    assert body["result_url"].endswith(".mp4")


def test_download_endpoint_serves_video_attachment(client):
    from app.routers import jobs as jobs_router

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
            r = client.get(f"/api/v1/jobs/download/{job_id}")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("video/mp4")
        assert "attachment" in r.headers["content-disposition"].lower()
        assert r.content == b"fake-mp4-data"
    finally:
        file_path.unlink(missing_ok=True)


def test_list_jobs_by_email_includes_download_url(client):
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
        r = client.get("/api/v1/jobs/by-email?email=test@example.com")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["download_url"] == f"/api/v1/jobs/download/{jobs[0]['id']}"

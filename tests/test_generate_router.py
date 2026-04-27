import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_retry_kie_task_retries_when_success_has_no_result_urls():
    from app.routers.generate import _retry_kie_task

    create_fn = AsyncMock(side_effect=["task-empty", "task-good"])
    kie = MagicMock()

    with patch(
        "app.routers.generate._poll_until_done",
        AsyncMock(side_effect=[
            {"state": "success", "result_urls": []},
            {"state": "success", "result_urls": ["https://cdn.kie.ai/final.mp4"]},
        ]),
    ), patch("app.routers.generate.asyncio.sleep", AsyncMock()):
        task_id, status = await _retry_kie_task(
            kie,
            create_fn,
            poll_is_video=True,
            max_retries=2,
        )

    assert task_id == "task-good"
    assert status["result_urls"] == ["https://cdn.kie.ai/final.mp4"]
    assert create_fn.await_count == 2


@pytest.mark.asyncio
async def test_process_video_checkpoints_extended_result_url():
    from app.routers.generate import VideoRequest, _process_video

    req = VideoRequest(
        email="test@example.com",
        image_url="https://app.phinodia.com/uploads/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
        description="Video UGC para crema facial",
        format="portrait",
        duration=15,
        product_name="Crema Glow",
        product_category="Skincare",
        pain_point="Piel reseca",
        creative_direction="Rutina matutina autentica",
        data_consent=True,
    )

    mock_script = MagicMock()
    mock_script.analyze_product = AsyncMock(return_value="50ml glass jar, exact white lid")
    mock_script.generate_buyer_persona = AsyncMock(return_value="Valentina, 27, Bogota")
    mock_script.generate_image_prompt = AsyncMock(return_value="first frame prompt")
    mock_script.generate_video_prompt = AsyncMock(return_value="base veo prompt")
    mock_script.generate_extension_prompt = AsyncMock(return_value="extension prompt")

    mock_kie = MagicMock()
    mock_kie.extend_video = AsyncMock(return_value="ext_task_1")

    update_calls = []

    async def fake_update(table, params, data):
        update_calls.append(data)
        return [data]

    with patch("app.routers.generate.ScriptGenerator", return_value=mock_script), patch(
        "app.routers.generate.KieAIClient", return_value=mock_kie
    ), patch(
        "app.routers.generate._retry_kie_task",
        AsyncMock(side_effect=[
            ("ff_task", {"state": "success", "result_urls": ["https://cdn.kie.ai/frame.png"]}),
            ("base_task", {"state": "success", "result_urls": ["https://cdn.kie.ai/base.mp4"]}),
        ]),
    ), patch(
        "app.routers.generate._poll_until_done",
        AsyncMock(return_value={"state": "success", "result_urls": ["https://cdn.kie.ai/full-15s.mp4"]}),
    ), patch("app.routers.generate.db.update", side_effect=fake_update), patch(
        "app.routers.generate.persist_external_url",
        AsyncMock(side_effect=lambda url, *_args: url),
    ), patch("app.routers.generate.asyncio.to_thread", AsyncMock(return_value=None)):
        await _process_video("job-123", req)

    assert any(call.get("result_url") == "https://cdn.kie.ai/full-15s.mp4" for call in update_calls)
    assert mock_script.generate_extension_prompt.await_count == 1
    assert mock_kie.extend_video.await_count == 1

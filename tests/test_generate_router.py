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
async def test_process_video_15s_completes_with_final_result_only():
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
    mock_script.compress_for_veo = AsyncMock(side_effect=lambda text: text)
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
        "app.routers.generate.is_video_duration_sufficient",
        AsyncMock(return_value=True),
    ), patch(
        "app.routers.generate.persist_external_url",
        AsyncMock(side_effect=lambda url, *_args: url),
    ), patch("app.routers.generate.asyncio.to_thread", AsyncMock(return_value=None)):
        await _process_video("job-123", req)

    assert any(call.get("result_url") == "https://cdn.kie.ai/full-15s.mp4" for call in update_calls)
    assert not any(call.get("result_url") == "https://cdn.kie.ai/base.mp4" for call in update_calls)
    assert mock_script.generate_extension_prompt.await_count == 1
    assert mock_kie.extend_video.await_count == 1
    assert mock_kie.extend_video.await_args.kwargs["model"] == "quality"


@pytest.mark.asyncio
async def test_process_video_15s_extension_failure_marks_failed_and_refunds():
    from app.routers.generate import VideoRequest, _process_video

    req = VideoRequest(
        email="test@example.com",
        image_url="https://app.phinodia.com/uploads/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
        description="Video UGC para depiladora",
        format="portrait",
        duration=15,
        product_name="Depiladora Flash",
        product_category="Belleza",
        pain_point="Poco tiempo",
        creative_direction="UGC selfie real",
        data_consent=True,
    )

    mock_script = MagicMock()
    mock_script.analyze_product = AsyncMock(return_value="analysis")
    mock_script.generate_buyer_persona = AsyncMock(return_value="persona")
    mock_script.generate_image_prompt = AsyncMock(return_value="first frame")
    mock_script.generate_video_prompt = AsyncMock(return_value="base prompt")
    mock_script.compress_for_veo = AsyncMock(side_effect=lambda text: text)
    mock_script.generate_extension_prompt = AsyncMock(return_value="extension prompt")

    mock_kie = MagicMock()
    mock_kie.extend_video = AsyncMock(return_value="ext-task")

    mock_credit = MagicMock()
    mock_credit.get_or_create_user = AsyncMock(return_value={"id": "user-1"})
    mock_credit.refund_credit = AsyncMock(return_value=True)

    update_calls = []

    async def fake_update(_table, _params, data):
        update_calls.append(data)
        return [data]

    with patch("app.routers.generate.ScriptGenerator", return_value=mock_script), patch(
        "app.routers.generate.KieAIClient", return_value=mock_kie
    ), patch(
        "app.routers.generate.CreditService", return_value=mock_credit
    ), patch(
        "app.routers.generate.VIDEO_RENDER_RETRIES", 1
    ), patch(
        "app.routers.generate._retry_kie_task",
        AsyncMock(side_effect=[
            ("ff-task", {"state": "success", "result_urls": ["https://cdn.kie.ai/frame.png"]}),
            ("base-task", {"state": "success", "result_urls": ["https://cdn.kie.ai/base.mp4"]}),
        ]),
    ), patch(
        "app.routers.generate._poll_until_done",
        AsyncMock(return_value={"state": "success", "result_urls": []}),
    ), patch(
        "app.routers.generate.db.update", side_effect=fake_update
    ), patch(
        "app.routers.generate.persist_external_url", AsyncMock(side_effect=lambda url, *_args: url)
    ):
        await _process_video("job-456", req)

    assert any(call.get("status") == "failed" for call in update_calls)
    assert not any(call.get("status") == "completed" for call in update_calls)
    mock_credit.refund_credit.assert_awaited_once_with("user-1", "video_15s")


@pytest.mark.asyncio
async def test_process_video_15s_short_final_video_marks_failed_and_refunds():
    from app.routers.generate import VideoRequest, _process_video

    req = VideoRequest(
        email="test@example.com",
        image_url="https://app.phinodia.com/uploads/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
        description="Video largo con producto exacto",
        format="portrait",
        duration=15,
        product_name="Crema Glow",
        product_category="Skincare",
        pain_point="Piel reseca",
        creative_direction="UGC selfie",
        data_consent=True,
    )

    mock_script = MagicMock()
    mock_script.analyze_product = AsyncMock(return_value="analysis")
    mock_script.generate_buyer_persona = AsyncMock(return_value="persona")
    mock_script.generate_image_prompt = AsyncMock(return_value="first frame")
    mock_script.generate_video_prompt = AsyncMock(return_value="base prompt")
    mock_script.compress_for_veo = AsyncMock(side_effect=lambda text: text)
    mock_script.generate_extension_prompt = AsyncMock(return_value="extension prompt")

    mock_kie = MagicMock()
    mock_kie.extend_video = AsyncMock(return_value="ext-task")

    mock_credit = MagicMock()
    mock_credit.get_or_create_user = AsyncMock(return_value={"id": "user-1"})
    mock_credit.refund_credit = AsyncMock(return_value=True)

    update_calls = []

    async def fake_update(_table, _params, data):
        update_calls.append(data)
        return [data]

    with patch("app.routers.generate.ScriptGenerator", return_value=mock_script), patch(
        "app.routers.generate.KieAIClient", return_value=mock_kie
    ), patch(
        "app.routers.generate.CreditService", return_value=mock_credit
    ), patch(
        "app.routers.generate.VIDEO_RENDER_RETRIES", 1
    ), patch(
        "app.routers.generate._retry_kie_task",
        AsyncMock(side_effect=[
            ("ff-task", {"state": "success", "result_urls": ["https://cdn.kie.ai/frame.png"]}),
            ("base-task", {"state": "success", "result_urls": ["https://cdn.kie.ai/base.mp4"]}),
        ]),
    ), patch(
        "app.routers.generate._poll_until_done",
        AsyncMock(return_value={"state": "success", "result_urls": ["https://cdn.kie.ai/full-15s.mp4"]}),
    ), patch(
        "app.routers.generate.db.update", side_effect=fake_update
    ), patch(
        "app.routers.generate.persist_external_url", AsyncMock(side_effect=lambda url, *_args: url)
    ), patch(
        "app.routers.generate.is_video_duration_sufficient", AsyncMock(return_value=False)
    ):
        await _process_video("job-789", req)

    assert any(call.get("status") == "failed" for call in update_calls)
    assert not any(call.get("status") == "completed" for call in update_calls)
    mock_credit.refund_credit.assert_awaited_once_with("user-1", "video_15s")


@pytest.mark.asyncio
async def test_retry_video_extension_retries_and_succeeds_on_second_attempt():
    from app.routers.generate import _retry_video_extension

    mock_kie = MagicMock()
    mock_kie.extend_video = AsyncMock(side_effect=["ext-task-1", "ext-task-2"])

    update_calls = []

    async def fake_update(_table, _params, data):
        update_calls.append(data)
        return [data]

    with patch(
        "app.routers.generate.db.update",
        side_effect=fake_update,
    ), patch(
        "app.routers.generate._poll_until_done",
        AsyncMock(side_effect=[
            {"state": "success", "result_urls": []},
            {"state": "success", "result_urls": ["https://cdn.kie.ai/full-15s.mp4"]},
        ]),
    ), patch("app.routers.generate.asyncio.sleep", AsyncMock()):
        task_id, status = await _retry_video_extension(
            kie=mock_kie,
            parent_task_id="base-task",
            prompt="extend prompt",
            job_id="job-999",
            max_retries=2,
        )

    assert task_id == "ext-task-2"
    assert status["result_urls"] == ["https://cdn.kie.ai/full-15s.mp4"]
    assert mock_kie.extend_video.await_count == 2
    assert update_calls[-1]["kie_task_id"] == "ext-task-2"


@pytest.mark.asyncio
async def test_process_video_uses_safe_prompt_fallback_after_base_failures():
    from app.routers.generate import VideoRequest, _process_video

    req = VideoRequest(
        email="test@example.com",
        image_url="https://app.phinodia.com/uploads/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
        description="UGC selfie para rasuradora",
        format="portrait",
        duration=15,
        product_name="Rasuradora",
        product_category="Belleza",
        pain_point="Depilarse toma tiempo",
        creative_direction="UGC selfie natural",
        data_consent=True,
    )

    mock_script = MagicMock()
    mock_script.analyze_product = AsyncMock(return_value="analysis")
    mock_script.generate_buyer_persona = AsyncMock(return_value="persona")
    mock_script.generate_image_prompt = AsyncMock(return_value="first frame")
    mock_script.generate_video_prompt = AsyncMock(return_value="X" * 650)
    mock_script.compress_for_veo = AsyncMock(return_value="compressed base prompt")
    mock_script.generate_extension_prompt = AsyncMock(return_value="extension prompt")

    mock_kie = MagicMock()
    mock_kie.extend_video = AsyncMock(return_value="ext-task")
    mock_credit = MagicMock()
    mock_credit.get_or_create_user = AsyncMock(return_value={"id": "user-1"})
    mock_credit.refund_credit = AsyncMock(return_value=True)

    update_calls = []

    async def fake_update(_table, _params, data):
        update_calls.append(data)
        return [data]

    video_attempts = {"count": 0}

    async def fake_retry_kie_task(_kie, create_fn, poll_is_video, max_retries=3, require_result_url=True):
        if not poll_is_video:
            return "ff-task", {"state": "success", "result_urls": ["https://cdn.kie.ai/frame.png"]}
        video_attempts["count"] += 1
        if video_attempts["count"] <= 2:
            return "", {"state": "failed", "result_urls": [], "error": "invalid payload"}
        return "base-task", {"state": "success", "result_urls": ["https://cdn.kie.ai/base.mp4"]}

    with patch("app.routers.generate.ScriptGenerator", return_value=mock_script), patch(
        "app.routers.generate.KieAIClient", return_value=mock_kie
    ), patch(
        "app.routers.generate.CreditService", return_value=mock_credit
    ), patch(
        "app.routers.generate.VIDEO_RENDER_RETRIES", 1
    ), patch(
        "app.routers.generate._retry_kie_task",
        AsyncMock(side_effect=fake_retry_kie_task),
    ), patch(
        "app.routers.generate._poll_until_done",
        AsyncMock(return_value={"state": "success", "result_urls": ["https://cdn.kie.ai/full-15s.mp4"]}),
    ), patch(
        "app.routers.generate.db.update", side_effect=fake_update
    ), patch(
        "app.routers.generate.is_video_duration_sufficient", AsyncMock(return_value=True)
    ), patch(
        "app.routers.generate.persist_external_url", AsyncMock(side_effect=lambda url, *_args: url)
    ), patch("app.routers.generate.asyncio.to_thread", AsyncMock(return_value=None)):
        await _process_video("job-safe-fallback", req)

    assert any(call.get("result_url") == "https://cdn.kie.ai/full-15s.mp4" for call in update_calls)
    assert mock_script.compress_for_veo.await_count >= 1


@pytest.mark.asyncio
async def test_process_video_completes_when_base_prompt_generation_raises():
    from app.routers.generate import VideoRequest, _process_video

    req = VideoRequest(
        email="test@example.com",
        image_url="https://app.phinodia.com/uploads/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
        description="UGC selfie para rasuradora",
        format="portrait",
        duration=15,
        product_name="Rasuradora",
        product_category="Belleza",
        pain_point="Depilarse toma tiempo",
        creative_direction="UGC selfie natural",
        data_consent=True,
    )

    mock_script = MagicMock()
    mock_script.analyze_product = AsyncMock(return_value="analysis")
    mock_script.generate_buyer_persona = AsyncMock(return_value="persona")
    mock_script.generate_image_prompt = AsyncMock(return_value="first frame")
    mock_script.generate_video_prompt = AsyncMock(side_effect=RuntimeError("openai down"))
    mock_script.compress_for_veo = AsyncMock(side_effect=lambda text: text)
    mock_script.generate_extension_prompt = AsyncMock(return_value="extension prompt")

    mock_kie = MagicMock()
    mock_kie.extend_video = AsyncMock(return_value="ext-task")

    update_calls = []

    async def fake_update(_table, _params, data):
        update_calls.append(data)
        return [data]

    with patch("app.routers.generate.ScriptGenerator", return_value=mock_script), patch(
        "app.routers.generate.KieAIClient", return_value=mock_kie
    ), patch(
        "app.routers.generate.VIDEO_RENDER_RETRIES", 1
    ), patch(
        "app.routers.generate._retry_kie_task",
        AsyncMock(side_effect=[
            ("ff-task", {"state": "success", "result_urls": ["https://cdn.kie.ai/frame.png"]}),
            ("base-task", {"state": "success", "result_urls": ["https://cdn.kie.ai/base.mp4"]}),
        ]),
    ), patch(
        "app.routers.generate._poll_until_done",
        AsyncMock(return_value={"state": "success", "result_urls": ["https://cdn.kie.ai/full-15s.mp4"]}),
    ), patch(
        "app.routers.generate.db.update", side_effect=fake_update
    ), patch(
        "app.routers.generate.is_video_duration_sufficient", AsyncMock(return_value=True)
    ), patch(
        "app.routers.generate.persist_external_url", AsyncMock(side_effect=lambda url, *_args: url)
    ), patch("app.routers.generate.asyncio.to_thread", AsyncMock(return_value=None)):
        await _process_video("job-base-prompt-fallback", req)

    assert any(call.get("status") == "completed" for call in update_calls)


@pytest.mark.asyncio
async def test_process_video_completes_when_extension_prompt_generation_raises():
    from app.routers.generate import VideoRequest, _process_video

    req = VideoRequest(
        email="test@example.com",
        image_url="https://app.phinodia.com/uploads/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
        description="UGC selfie para rasuradora",
        format="portrait",
        duration=15,
        product_name="Rasuradora",
        product_category="Belleza",
        pain_point="Depilarse toma tiempo",
        creative_direction="UGC selfie natural",
        data_consent=True,
    )

    mock_script = MagicMock()
    mock_script.analyze_product = AsyncMock(return_value="analysis")
    mock_script.generate_buyer_persona = AsyncMock(return_value="persona")
    mock_script.generate_image_prompt = AsyncMock(return_value="first frame")
    mock_script.generate_video_prompt = AsyncMock(return_value="base prompt")
    mock_script.compress_for_veo = AsyncMock(side_effect=lambda text: text)
    mock_script.generate_extension_prompt = AsyncMock(side_effect=RuntimeError("openai timeout"))

    mock_kie = MagicMock()
    mock_kie.extend_video = AsyncMock(return_value="ext-task")

    update_calls = []

    async def fake_update(_table, _params, data):
        update_calls.append(data)
        return [data]

    with patch("app.routers.generate.ScriptGenerator", return_value=mock_script), patch(
        "app.routers.generate.KieAIClient", return_value=mock_kie
    ), patch(
        "app.routers.generate.VIDEO_RENDER_RETRIES", 1
    ), patch(
        "app.routers.generate._retry_kie_task",
        AsyncMock(side_effect=[
            ("ff-task", {"state": "success", "result_urls": ["https://cdn.kie.ai/frame.png"]}),
            ("base-task", {"state": "success", "result_urls": ["https://cdn.kie.ai/base.mp4"]}),
        ]),
    ), patch(
        "app.routers.generate._poll_until_done",
        AsyncMock(return_value={"state": "success", "result_urls": ["https://cdn.kie.ai/full-15s.mp4"]}),
    ), patch(
        "app.routers.generate.db.update", side_effect=fake_update
    ), patch(
        "app.routers.generate.is_video_duration_sufficient", AsyncMock(return_value=True)
    ), patch(
        "app.routers.generate.persist_external_url", AsyncMock(side_effect=lambda url, *_args: url)
    ), patch("app.routers.generate.asyncio.to_thread", AsyncMock(return_value=None)):
        await _process_video("job-ext-prompt-fallback", req)

    assert any(call.get("status") == "completed" for call in update_calls)


@pytest.mark.asyncio
async def test_process_video_completes_when_early_openai_steps_raise():
    from app.routers.generate import VideoRequest, _process_video

    req = VideoRequest(
        email="test@example.com",
        image_url="https://app.phinodia.com/uploads/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
        description="UGC selfie para rasuradora",
        format="portrait",
        duration=15,
        product_name="Rasuradora",
        product_category="Belleza",
        pain_point="Depilarse toma tiempo",
        creative_direction="UGC selfie natural",
        data_consent=True,
    )

    mock_script = MagicMock()
    mock_script.analyze_product = AsyncMock(side_effect=RuntimeError("analysis down"))
    mock_script.generate_buyer_persona = AsyncMock(side_effect=RuntimeError("persona down"))
    mock_script.generate_image_prompt = AsyncMock(side_effect=RuntimeError("image prompt down"))
    mock_script.generate_video_prompt = AsyncMock(side_effect=RuntimeError("video prompt down"))
    mock_script.compress_for_veo = AsyncMock(side_effect=lambda text: text)
    mock_script.generate_extension_prompt = AsyncMock(side_effect=RuntimeError("extension prompt down"))

    mock_kie = MagicMock()
    mock_kie.extend_video = AsyncMock(return_value="ext-task")

    update_calls = []

    async def fake_update(_table, _params, data):
        update_calls.append(data)
        return [data]

    with patch("app.routers.generate.ScriptGenerator", return_value=mock_script), patch(
        "app.routers.generate.KieAIClient", return_value=mock_kie
    ), patch(
        "app.routers.generate.VIDEO_RENDER_RETRIES", 1
    ), patch(
        "app.routers.generate._retry_kie_task",
        AsyncMock(side_effect=[
            ("ff-task", {"state": "success", "result_urls": ["https://cdn.kie.ai/frame.png"]}),
            ("base-task", {"state": "success", "result_urls": ["https://cdn.kie.ai/base.mp4"]}),
        ]),
    ), patch(
        "app.routers.generate._poll_until_done",
        AsyncMock(return_value={"state": "success", "result_urls": ["https://cdn.kie.ai/full-15s.mp4"]}),
    ), patch(
        "app.routers.generate.db.update", side_effect=fake_update
    ), patch(
        "app.routers.generate.is_video_duration_sufficient", AsyncMock(return_value=True)
    ), patch(
        "app.routers.generate.persist_external_url", AsyncMock(side_effect=lambda url, *_args: url)
    ), patch("app.routers.generate.asyncio.to_thread", AsyncMock(return_value=None)):
        await _process_video("job-all-openai-fallbacks", req)

    assert any(call.get("status") == "completed" for call in update_calls)

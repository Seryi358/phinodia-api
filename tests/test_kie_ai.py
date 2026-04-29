import pytest
from unittest.mock import AsyncMock, patch
import httpx


@pytest.mark.asyncio
async def test_create_video_task():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={"code": 200, "msg": "success", "data": {"taskId": "task_veo_123"}},
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)

        client = KieAIClient(api_key="test-key")
        task_id = await client.create_video_task(
            prompt="A woman holds a cream jar showing the label",
            image_url="https://example.com/product.jpg",
            aspect_ratio="9:16",
        )
        assert task_id == "task_veo_123"

        call_args = MockClient.return_value.post.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert "/veo/generate" in url
        body = call_args.kwargs["json"]
        assert body["model"] == "veo3"  # Quality mode default
        assert body["generationType"] == "FIRST_AND_LAST_FRAMES_2_VIDEO"
        assert body["aspect_ratio"] == "9:16"
        assert body["imageUrls"] == ["https://example.com/product.jpg"]
        assert body["enableTranslation"] is True
        assert body["quality"] == "high"


@pytest.mark.asyncio
async def test_create_video_task_surfaces_http_error_body():
    from app.services.kie_ai import KieAIClient

    error_response = httpx.Response(
        400,
        json={"code": 400, "msg": "failed to fetch image due to access limits"},
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=error_response)

        client = KieAIClient(api_key="test-key")
        with pytest.raises(httpx.HTTPError, match="failed to fetch image due to access limits"):
            await client.create_video_task(
                prompt="A woman holds a cream jar showing the label",
                image_url="https://example.com/product.jpg",
                aspect_ratio="9:16",
            )


@pytest.mark.asyncio
async def test_create_video_task_quality_model():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={"code": 200, "msg": "success", "data": {"taskId": "task_veo_q"}},
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)

        client = KieAIClient(api_key="test-key")
        task_id = await client.create_video_task(
            prompt="Product showcase",
            image_url="https://example.com/product.jpg",
            aspect_ratio="16:9",
            model="veo3",
        )
        assert task_id == "task_veo_q"

        call_args = MockClient.return_value.post.call_args
        body = call_args.kwargs["json"]
        assert body["model"] == "veo3"


@pytest.mark.asyncio
async def test_extend_video():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={"code": 200, "msg": "success", "data": {"taskId": "task_ext_456"}},
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)

        client = KieAIClient(api_key="test-key")
        task_id = await client.extend_video(
            task_id="task_veo_123",
            prompt="Continue showing the product application",
            model="quality",
        )
        assert task_id == "task_ext_456"

        call_args = MockClient.return_value.post.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert "/veo/extend" in url
        body = call_args.kwargs["json"]
        assert body["taskId"] == "task_veo_123"
        assert body["model"] == "quality"


@pytest.mark.asyncio
async def test_get_video_status_success():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={
            "code": 200,
            "msg": "success",
            "data": {
                "taskId": "task_123",
                "successFlag": 1,
                "videoUrl": "https://cdn.kie.ai/result.mp4",
            },
        },
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)

        client = KieAIClient(api_key="test-key")
        status = await client.get_video_status("task_123")

        call_args = MockClient.return_value.get.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert "/veo/record-info" in url

        assert status["state"] == "success"
        assert status["result_urls"] == ["https://cdn.kie.ai/result.mp4"]
        assert status["progress"] == 100


@pytest.mark.asyncio
async def test_get_video_status_prefers_full_result_urls():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={
            "code": 200,
            "msg": "success",
            "data": {
                "taskId": "task_123",
                "successFlag": 1,
                "response": {
                    "resultUrls": ["https://cdn.kie.ai/base-8s.mp4"],
                    "fullResultUrls": ["https://cdn.kie.ai/full-15s.mp4"],
                },
            },
        },
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)

        client = KieAIClient(api_key="test-key")
        status = await client.get_video_status("task_123")

        assert status["state"] == "success"
        assert status["result_urls"] == ["https://cdn.kie.ai/full-15s.mp4"]
        assert status["progress"] == 100


@pytest.mark.asyncio
async def test_get_video_status_generating():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={
            "code": 200,
            "msg": "success",
            "data": {
                "taskId": "task_123",
                "successFlag": 0,
                "videoUrl": "",
            },
        },
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)

        client = KieAIClient(api_key="test-key")
        status = await client.get_video_status("task_123")
        assert status["state"] == "generating"
        assert status["result_urls"] == []
        assert status["progress"] == 50


@pytest.mark.asyncio
async def test_get_video_status_failed():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={
            "code": 200,
            "msg": "success",
            "data": {
                "taskId": "task_123",
                "successFlag": 2,
                "videoUrl": "",
                "errorCode": "CLIENT_ERROR",
                "errorMessage": "non-English prompt detected",
            },
        },
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)

        client = KieAIClient(api_key="test-key")
        status = await client.get_video_status("task_123")
        assert status["state"] == "failed"
        assert status["result_urls"] == []
        assert status["progress"] == 0
        assert status["error"] == "CLIENT_ERROR: non-English prompt detected"


@pytest.mark.asyncio
async def test_get_task_status_includes_fail_message():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={
            "code": 200,
            "msg": "success",
            "data": {
                "taskId": "task_123",
                "state": "fail",
                "resultJson": "",
                "progress": 0,
                "failCode": "FETCH_ERROR",
                "failMsg": "failed to fetch image due to access limits",
            },
        },
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)

        client = KieAIClient(api_key="test-key")
        status = await client.get_task_status("task_123")
        assert status["state"] == "fail"
        assert status["result_urls"] == []
        assert status["error"] == "FETCH_ERROR: failed to fetch image due to access limits"


@pytest.mark.asyncio
async def test_get_hd_video():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={
            "code": 200,
            "msg": "success",
            "data": {"resultUrl": "https://cdn.kie.ai/hd-result.mp4"},
        },
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)

        client = KieAIClient(api_key="test-key")
        url = await client.get_hd_video("task_123")
        assert url == "https://cdn.kie.ai/hd-result.mp4"

        call_args = MockClient.return_value.get.call_args
        req_url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert "/veo/get-1080p-video" in req_url

@pytest.mark.asyncio
async def test_create_image_task():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={"code": 200, "msg": "success", "data": {"taskId": "task_img_123"}},
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)

        client = KieAIClient(api_key="test-key")
        task_id = await client.create_image_task(
            prompt="Product cream in natural kitchen setting",
            image_url="https://example.com/product.jpg",
            aspect_ratio="1:1",
        )
        assert task_id == "task_img_123"

        call_args = MockClient.return_value.post.call_args
        body = call_args.kwargs["json"]
        assert body["model"] == "gpt-image-2-image-to-image"
        assert body["input"]["input_urls"] == ["https://example.com/product.jpg"]
        assert body["input"]["aspect_ratio"] == "1:1"
        assert body["input"]["nsfw_checker"] is False


@pytest.mark.asyncio
async def test_create_image_task_text_to_image_when_reference_missing():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={"code": 200, "msg": "success", "data": {"taskId": "task_img_txt_123"}},
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)

        client = KieAIClient(api_key="test-key")
        task_id = await client.create_image_task(
            prompt="Premium skincare product on a marble vanity",
            image_url="",
            aspect_ratio="4:5",
        )
        assert task_id == "task_img_txt_123"

        call_args = MockClient.return_value.post.call_args
        body = call_args.kwargs["json"]
        assert body["model"] == "gpt-image-2-text-to-image"
        assert "input_urls" not in body["input"]
        assert body["input"]["aspect_ratio"] == "4:5"
        assert body["input"]["nsfw_checker"] is False


@pytest.mark.asyncio
async def test_get_task_status_success():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={
            "code": 200,
            "msg": "success",
            "data": {
                "taskId": "task_123",
                "state": "success",
                "resultJson": '{"resultUrls":["https://cdn.kie.ai/result.jpg"]}',
                "progress": 100,
            },
        },
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)

        client = KieAIClient(api_key="test-key")
        status = await client.get_task_status("task_123")
        assert status["state"] == "success"
        assert status["result_urls"] == ["https://cdn.kie.ai/result.jpg"]
        assert status["progress"] == 100


@pytest.mark.asyncio
async def test_get_task_status_generating():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={
            "code": 200,
            "msg": "success",
            "data": {
                "taskId": "task_123",
                "state": "generating",
                "resultJson": "",
                "progress": 45,
            },
        },
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)

        client = KieAIClient(api_key="test-key")
        status = await client.get_task_status("task_123")
        assert status["state"] == "generating"
        assert status["result_urls"] == []
        assert status["progress"] == 45


@pytest.mark.asyncio
async def test_get_task_status_supports_urls_alias():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={
            "code": 200,
            "msg": "success",
            "data": {
                "taskId": "task_123",
                "state": "success",
                "resultJson": '{"urls":["https://cdn.kie.ai/result-alt.jpg"]}',
                "progress": 100,
            },
        },
    )
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)

        client = KieAIClient(api_key="test-key")
        status = await client.get_task_status("task_123")
        assert status["state"] == "success"
        assert status["result_urls"] == ["https://cdn.kie.ai/result-alt.jpg"]

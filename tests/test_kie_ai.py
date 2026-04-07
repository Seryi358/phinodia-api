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
        assert body["model"] == "veo3_fast"
        assert body["generationType"] == "FIRST_AND_LAST_FRAMES_2_VIDEO"
        assert body["aspect_ratio"] == "9:16"
        assert body["imageUrls"] == ["https://example.com/product.jpg"]
        assert body["enableTranslation"] is False


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
            model="fast",
        )
        assert task_id == "task_ext_456"

        call_args = MockClient.return_value.post.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert "/veo/extend" in url
        body = call_args.kwargs["json"]
        assert body["taskId"] == "task_veo_123"
        assert body["model"] == "fast"


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


@pytest.mark.asyncio
async def test_get_hd_video():
    from app.services.kie_ai import KieAIClient

    mock_response = httpx.Response(
        200,
        json={
            "code": 200,
            "msg": "success",
            "data": {"videoUrl": "https://cdn.kie.ai/hd-result.mp4"},
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
        assert body["model"] == "nano-banana-pro"
        assert body["input"]["image_input"] == ["https://example.com/product.jpg"]
        assert body["input"]["resolution"] == "2K"


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

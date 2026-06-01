import pytest
from unittest.mock import AsyncMock, patch
import httpx


def _mock_client(MockClient, *, post=None, get=None):
    MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
    MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
    if post is not None:
        MockClient.return_value.post = AsyncMock(return_value=post)
    if get is not None:
        MockClient.return_value.get = AsyncMock(return_value=get)


# ── Gemini Omni video ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_omni_video_task():
    from app.services.kie_ai import KieAIClient
    resp = httpx.Response(200, json={"code": 200, "msg": "success", "data": {"taskId": "task_omni_1"}})
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        _mock_client(MockClient, post=resp)
        client = KieAIClient(api_key="test-key")
        task_id = await client.create_omni_video_task(
            prompt="vertical UGC selfie; she speaks Colombian Spanish",
            image_url="https://example.com/frame.png",
            seed=778899, duration="10", aspect_ratio="9:16", resolution="720p",
        )
        assert task_id == "task_omni_1"
        call = MockClient.return_value.post.call_args
        url = call.args[0] if call.args else call.kwargs.get("url", "")
        assert "/jobs/createTask" in url
        body = call.kwargs["json"]
        assert body["model"] == "gemini-omni-video"
        inp = body["input"]
        assert inp["duration"] == "10"
        assert inp["aspect_ratio"] == "9:16"
        assert inp["resolution"] == "720p"
        assert inp["image_urls"] == ["https://example.com/frame.png"]
        assert inp["seed"] == 778899


@pytest.mark.asyncio
async def test_create_omni_video_task_with_character_ids():
    from app.services.kie_ai import KieAIClient
    resp = httpx.Response(200, json={"code": 200, "msg": "success", "data": {"taskId": "t2"}})
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        _mock_client(MockClient, post=resp)
        client = KieAIClient(api_key="test-key")
        await client.create_omni_video_task(prompt="p", character_ids=["c1", "c2", "c3", "c4"])
        inp = MockClient.return_value.post.call_args.kwargs["json"]["input"]
        # capped at 3
        assert inp["character_ids"] == ["c1", "c2", "c3"]
        # no image -> no image_urls key
        assert "image_urls" not in inp


@pytest.mark.asyncio
async def test_create_omni_character_returns_id():
    from app.services.kie_ai import KieAIClient
    resp = httpx.Response(200, json={"code": 200, "msg": "success",
                                     "data": {"characterId": "char_abc", "characterName": "x"}})
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        _mock_client(MockClient, post=resp)
        client = KieAIClient(api_key="test-key")
        cid = await client.create_omni_character(descriptions="a Colombian woman",
                                                 image_url="https://example.com/p.png")
        assert cid == "char_abc"
        body = MockClient.return_value.post.call_args.kwargs["json"]
        # both doc-variant keys are sent defensively
        assert body["descriptions"] == "a Colombian woman"
        assert body["description"] == "a Colombian woman"
        assert body["image_urls"] == ["https://example.com/p.png"]


@pytest.mark.asyncio
async def test_create_omni_character_best_effort_returns_none_on_error():
    from app.services.kie_ai import KieAIClient
    resp = httpx.Response(500, json={"code": 500, "msg": "server error"})
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        _mock_client(MockClient, post=resp)
        client = KieAIClient(api_key="test-key")
        cid = await client.create_omni_character(descriptions="x", image_url="https://e.com/p.png")
        assert cid is None  # never raises — character is an optional consistency aid


# ── Images (GPT Image 2) — unchanged endpoint ─────────────────────

@pytest.mark.asyncio
async def test_create_image_task():
    from app.services.kie_ai import KieAIClient
    resp = httpx.Response(200, json={"code": 200, "msg": "success", "data": {"taskId": "task_img_123"}})
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        _mock_client(MockClient, post=resp)
        client = KieAIClient(api_key="test-key")
        task_id = await client.create_image_task(
            prompt="Product cream in natural kitchen setting",
            image_url="https://example.com/product.jpg", aspect_ratio="1:1")
        assert task_id == "task_img_123"
        body = MockClient.return_value.post.call_args.kwargs["json"]
        assert body["model"] == "gpt-image-2-image-to-image"
        assert body["input"]["input_urls"] == ["https://example.com/product.jpg"]
        assert body["input"]["aspect_ratio"] == "1:1"
        assert body["input"]["nsfw_checker"] is False


@pytest.mark.asyncio
async def test_create_image_task_text_to_image_when_reference_missing():
    from app.services.kie_ai import KieAIClient
    resp = httpx.Response(200, json={"code": 200, "msg": "success", "data": {"taskId": "task_txt"}})
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        _mock_client(MockClient, post=resp)
        client = KieAIClient(api_key="test-key")
        task_id = await client.create_image_task(prompt="Premium skincare on marble",
                                                 image_url="", aspect_ratio="4:5")
        assert task_id == "task_txt"
        body = MockClient.return_value.post.call_args.kwargs["json"]
        assert body["model"] == "gpt-image-2-text-to-image"
        assert "input_urls" not in body["input"]
        assert body["input"]["aspect_ratio"] == "4:5"


# ── Unified task polling (used by omni video AND images) ──────────

@pytest.mark.asyncio
async def test_get_task_status_success():
    from app.services.kie_ai import KieAIClient
    resp = httpx.Response(200, json={"code": 200, "msg": "success", "data": {
        "taskId": "task_123", "state": "success",
        "resultJson": '{"resultUrls":["https://cdn.kie.ai/result.mp4"]}',
        "progress": 100, "creditsConsumed": 180}})
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        _mock_client(MockClient, get=resp)
        client = KieAIClient(api_key="test-key")
        status = await client.get_task_status("task_123")
        call = MockClient.return_value.get.call_args
        url = call.args[0] if call.args else call.kwargs.get("url", "")
        assert "/jobs/recordInfo" in url
        assert status["state"] == "success"
        assert status["result_urls"] == ["https://cdn.kie.ai/result.mp4"]
        assert status["credits"] == 180


@pytest.mark.asyncio
async def test_get_task_status_generating():
    from app.services.kie_ai import KieAIClient
    resp = httpx.Response(200, json={"code": 200, "msg": "success", "data": {
        "taskId": "task_123", "state": "generating", "resultJson": "", "progress": 45}})
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        _mock_client(MockClient, get=resp)
        client = KieAIClient(api_key="test-key")
        status = await client.get_task_status("task_123")
        assert status["state"] == "generating"
        assert status["result_urls"] == []
        assert status["progress"] == 45


@pytest.mark.asyncio
async def test_get_task_status_includes_fail_message():
    from app.services.kie_ai import KieAIClient
    resp = httpx.Response(200, json={"code": 200, "msg": "success", "data": {
        "taskId": "task_123", "state": "fail", "resultJson": "", "progress": 0,
        "failCode": "FETCH_ERROR", "failMsg": "failed to fetch image due to access limits"}})
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        _mock_client(MockClient, get=resp)
        client = KieAIClient(api_key="test-key")
        status = await client.get_task_status("task_123")
        assert status["state"] == "fail"
        assert status["result_urls"] == []
        assert status["error"] == "FETCH_ERROR: failed to fetch image due to access limits"


@pytest.mark.asyncio
async def test_get_task_status_supports_urls_alias():
    from app.services.kie_ai import KieAIClient
    resp = httpx.Response(200, json={"code": 200, "msg": "success", "data": {
        "taskId": "task_123", "state": "success",
        "resultJson": '{"urls":["https://cdn.kie.ai/result-alt.jpg"]}', "progress": 100}})
    with patch("app.services.kie_ai.httpx.AsyncClient") as MockClient:
        _mock_client(MockClient, get=resp)
        client = KieAIClient(api_key="test-key")
        status = await client.get_task_status("task_123")
        assert status["state"] == "success"
        assert status["result_urls"] == ["https://cdn.kie.ai/result-alt.jpg"]

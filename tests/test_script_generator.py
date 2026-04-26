import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_generate_video_prompt():
    from app.services.script_generator import ScriptGenerator

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "A woman picks up the cream jar from a kitchen counter..."

    with patch("app.services.script_generator.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        gen = ScriptGenerator(api_key="test-key")
        prompt = await gen.generate_video_prompt(
            product_name="Crema Facial Glow",
            description="Crema hidratante con vitamina C para rostro",
            duration=15,
            format_type="portrait",
            creative_direction="Show a young woman using the cream in her morning routine",
            product_analysis="Premium facial cream, 50ml jar...",
            buyer_persona="Sofia, 28, Bogota, skincare enthusiast...",
        )

        assert prompt == "A woman picks up the cream jar from a kitchen counter..."
        call_args = mock_client.chat.completions.create.call_args
        assert call_args.kwargs["model"] == "gpt-4o"
        messages = call_args.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert "AIDA" in messages[0]["content"]
        assert "UGC" in messages[0]["content"]
        assert "VEO 3.1" in messages[0]["content"]
        # Verify product_analysis and buyer_persona are in user message
        assert "Premium facial cream" in messages[1]["content"]
        assert "Sofia, 28" in messages[1]["content"]


@pytest.mark.asyncio
async def test_generate_video_prompt_without_analysis():
    """Video prompt works without product_analysis/buyer_persona (backwards compat)."""
    from app.services.script_generator import ScriptGenerator

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "UGC video prompt..."

    with patch("app.services.script_generator.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        gen = ScriptGenerator(api_key="test-key")
        prompt = await gen.generate_video_prompt(
            product_name="Crema Glow",
            description="Crema hidratante",
            duration=8,
            format_type="portrait",
        )
        assert prompt == "UGC video prompt..."
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert "Not available" in messages[1]["content"]


@pytest.mark.asyncio
async def test_generate_image_prompt():
    from app.services.script_generator import ScriptGenerator

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "A cream jar on a marble bathroom counter..."

    with patch("app.services.script_generator.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        gen = ScriptGenerator(api_key="test-key")
        prompt = await gen.generate_image_prompt(
            product_name="Crema Facial Glow",
            description="Crema hidratante con vitamina C",
            aspect_ratio="1:1",
            creative_direction="Natural bathroom setting",
            product_analysis="50ml glass jar, frosted finish, white lid, premium skincare packaging",
        )

        assert "cream jar" in prompt.lower() or "marble" in prompt.lower()
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        # New image prompt uses GPT Image 2 (not iPhone-specific)
        assert "GPT Image 2" in messages[0]["content"]
        assert "50ml glass jar" in messages[1]["content"]
        assert "SCENE:" in messages[0]["content"]


@pytest.mark.asyncio
async def test_generate_image_prompt_ugc_uses_buyer_persona_and_realism_cues():
    from app.services.script_generator import ScriptGenerator

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "SCENE: candid bathroom selfie..."

    with patch("app.services.script_generator.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        gen = ScriptGenerator(api_key="test-key")
        prompt = await gen.generate_image_prompt(
            product_name="Crema Facial Glow",
            description="Crema hidratante para rutina nocturna",
            aspect_ratio="9:16",
            creative_direction="Instagram selfie showing the product naturally",
            product_analysis="50ml glass jar, rounded shoulders, satin label, matte lid",
            buyer_persona="Valentina, 27, Bogota, skincare creator with warm and natural vibe",
            prompt_mode="ugc",
        )

        assert "SCENE:" in prompt
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert "Instagram-style UGC selfie" in messages[1]["content"]
        assert "Valentina, 27" in messages[1]["content"]
        assert "50ml glass jar" in messages[1]["content"]
        assert "captured like a real phone selfie" in messages[1]["content"]
        assert "not a fashion model" in messages[1]["content"]
        assert "do not rewrite" in messages[1]["content"]


@pytest.mark.asyncio
async def test_generate_image_prompt_first_frame_uses_video_seed_mode():
    from app.services.script_generator import ScriptGenerator

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "SCENE: vertical selfie first frame..."

    with patch("app.services.script_generator.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        gen = ScriptGenerator(api_key="test-key")
        prompt = await gen.generate_image_prompt(
            product_name="Crema Facial Glow",
            description="UGC ad first frame",
            aspect_ratio="9:16",
            creative_direction="Front-camera selfie first frame, phone out of frame",
            product_analysis="50ml glass jar, frosted body, white cap",
            buyer_persona="Sofia, 28, Bogota, skincare enthusiast",
            prompt_mode="video_first_frame",
        )

        assert "vertical selfie first frame" in prompt.lower()
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert "FIRST FRAME" in messages[1]["content"]
        assert "Sofia, 28" in messages[1]["content"]
        assert "phone out of frame" in messages[1]["content"]
        assert "everyday customer" in messages[1]["content"]


@pytest.mark.asyncio
async def test_generate_image_prompt_landing_mode_uses_shot_brief():
    from app.services.script_generator import ScriptGenerator

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "SCENE: premium landing hero..."

    with patch("app.services.script_generator.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        gen = ScriptGenerator(api_key="test-key")
        prompt = await gen.generate_image_prompt(
            product_name="Crema Facial Glow",
            description="Landing hero for skincare product",
            aspect_ratio="16:9",
            creative_direction="Centered hero banner with breathing room for copy",
            product_analysis="50ml glass jar, premium finish",
            buyer_persona="Mujeres 25-35 en Colombia",
            prompt_mode="landing_hero",
        )

        assert "landing hero" in prompt.lower()
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert "landing-page gallery image" in messages[1]["content"]
        assert "Centered hero banner" in messages[1]["content"]
        assert "Mujeres 25-35" in messages[1]["content"]


@pytest.mark.asyncio
async def test_generate_landing_page():
    """Landings call Claude Opus 4.6 via the official Anthropic API.
    The test sets ANTHROPIC_API_KEY in settings cache so the Opus path
    fires (instead of falling back to OpenAI on missing key)."""
    from app.services.script_generator import ScriptGenerator
    from app.config import get_settings

    # Inject a fake anthropic key into the cached Settings instance so the
    # Opus call doesn't immediately raise + fall back to OpenAI.
    settings = get_settings()
    original = settings.anthropic_api_key
    settings.anthropic_api_key = "sk-ant-test-key-1234567890"

    landing_html = "<!DOCTYPE html><html><head><title>Glow Cream</title></head><body>...</body></html>"
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={
        "content": [{"type": "text", "text": landing_html}],
    })

    try:
        with patch("app.services.script_generator.httpx.AsyncClient") as MockClient:
            mock_inst = MagicMock()
            mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
            mock_inst.__aexit__ = AsyncMock(return_value=False)
            mock_inst.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_inst

            gen = ScriptGenerator(api_key="test-key")
            html = await gen.generate_landing_page(
                product_name="Crema Facial Glow",
                description="Crema hidratante premium",
                image_url="https://example.com/cream.jpg",
                style_preference="modern, dark theme",
            )

            assert html == landing_html
            post_call = mock_inst.post.call_args
            url = post_call.args[0]
            body = post_call.kwargs["json"]
            headers = post_call.kwargs["headers"]
            # Now hits api.anthropic.com directly, not KIE.
            assert url == "https://api.anthropic.com/v1/messages"
            assert headers["x-api-key"] == "sk-ant-test-key-1234567890"
            assert "anthropic-version" in headers
            assert body["model"] == "claude-opus-4-7"
            assert body["max_tokens"] >= 16000
            assert "BUYER PERSONA" in body["messages"][0]["content"]
            assert "Crema Facial Glow" in body["messages"][0]["content"]
    finally:
        settings.anthropic_api_key = original


@pytest.mark.asyncio
async def test_analyze_product():
    from app.services.script_generator import ScriptGenerator

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "## PHYSICAL DIMENSIONS\n50ml glass jar, 7cm height..."

    with patch("app.services.script_generator.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        gen = ScriptGenerator(api_key="test-key")
        analysis = await gen.analyze_product(
            product_name="Crema Facial Glow",
            description="Crema hidratante con vitamina C",
            image_url="https://example.com/product.jpg",
        )

        assert "50ml" in analysis
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert "product intelligence analyst" in messages[0]["content"].lower()
        user_content = messages[1]["content"]
        assert isinstance(user_content, list)
        assert user_content[0]["type"] == "text"
        assert "Crema Facial Glow" in user_content[0]["text"]
        assert user_content[1]["type"] == "image_url"
        assert user_content[1]["image_url"]["url"] == "https://example.com/product.jpg"


@pytest.mark.asyncio
async def test_generate_buyer_persona():
    from app.services.script_generator import ScriptGenerator

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Sofia Martinez, 28, Bogota, content creator..."

    with patch("app.services.script_generator.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        gen = ScriptGenerator(api_key="test-key")
        persona = await gen.generate_buyer_persona(
            product_name="Crema Facial Glow",
            product_analysis="50ml glass jar, premium facial cream...",
        )

        assert "Sofia" in persona
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        # Persona prompt is in Spanish: "Director de Casting"
        assert "director de casting" in messages[0]["content"].lower()
        assert "50ml glass jar" in messages[1]["content"]


@pytest.mark.asyncio
async def test_generate_extension_prompt():
    from app.services.script_generator import ScriptGenerator

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "The woman now applies the cream gently..."

    with patch("app.services.script_generator.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        gen = ScriptGenerator(api_key="test-key")
        ext_prompt = await gen.generate_extension_prompt(
            original_prompt="A woman picks up cream jar from kitchen counter...",
            extension_number=1,
            duration=15,
        )

        assert "applies" in ext_prompt.lower()
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        # User message (not system) carries time range + the user-controlled
        # original prompt — system stays static so injected instructions in
        # the original_prompt can't override our guidance.
        assert "0-8" in messages[1]["content"]
        assert "continuation #1" in messages[1]["content"].lower()

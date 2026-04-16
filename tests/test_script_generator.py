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
        )

        assert "cream jar" in prompt.lower() or "marble" in prompt.lower()
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        # New image prompt uses Nano Banana 2 (not iPhone-specific)
        assert "Nano Banana" in messages[0]["content"]


@pytest.mark.asyncio
async def test_generate_landing_page():
    from app.services.script_generator import ScriptGenerator

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "<!DOCTYPE html><html><head><title>Glow Cream</title></head><body>...</body></html>"

    with patch("app.services.script_generator.AsyncOpenAI") as MockOpenAI:
        mock_client = AsyncMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        gen = ScriptGenerator(api_key="test-key")
        html = await gen.generate_landing_page(
            product_name="Crema Facial Glow",
            description="Crema hidratante premium",
            image_url="https://example.com/cream.jpg",
            style_preference="modern, dark theme",
        )

        assert "<!DOCTYPE html>" in html
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        # AIDA appears in user template, not system prompt for landing
        assert "AIDA" in messages[1]["content"]


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
        )

        assert "50ml" in analysis
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert "product intelligence analyst" in messages[0]["content"].lower()
        assert "Crema Facial Glow" in messages[1]["content"]


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
        # System prompt should reference the time range
        assert "0-8" in messages[0]["content"]
        assert "extension #1" in messages[1]["content"].lower()

from openai import AsyncOpenAI
from app.prompts.video_ugc import SYSTEM_PROMPT as VIDEO_SYSTEM, USER_TEMPLATE as VIDEO_USER
from app.prompts.image_product import SYSTEM_PROMPT as IMAGE_SYSTEM, USER_TEMPLATE as IMAGE_USER
from app.prompts.landing_page import SYSTEM_PROMPT as LANDING_SYSTEM, USER_TEMPLATE as LANDING_USER

ASPECT_RATIOS = {"portrait": "9:16", "landscape": "16:9"}


class ScriptGenerator:
    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(api_key=api_key)

    async def _call_gpt(self, system: str, user: str, max_tokens: int = 2000) -> str:
        response = await self.client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.8,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()

    async def analyze_product(self, product_name: str, description: str) -> str:
        """Step 1: Deep product analysis from image description."""
        from app.prompts.product_analyzer import SYSTEM_PROMPT, USER_TEMPLATE
        user_msg = USER_TEMPLATE.format(product_name=product_name, description=description)
        return await self._call_gpt(SYSTEM_PROMPT, user_msg)

    async def generate_buyer_persona(self, product_name: str, product_analysis: str) -> str:
        """Step 2: Generate ideal UGC creator persona."""
        from app.prompts.buyer_persona import SYSTEM_PROMPT, USER_TEMPLATE
        user_msg = USER_TEMPLATE.format(product_name=product_name, product_analysis=product_analysis)
        return await self._call_gpt(SYSTEM_PROMPT, user_msg)

    async def generate_extension_prompt(self, original_prompt: str, extension_number: int, duration: int) -> str:
        """Generate continuation prompt for video extension."""
        seconds_so_far = 8 + (extension_number - 1) * 7
        seconds_remaining = duration - seconds_so_far
        system = f"""You are continuing a UGC video script. The video so far covers 0-{seconds_so_far} seconds.
Now generate the continuation for the next {min(7, seconds_remaining)} seconds (seconds {seconds_so_far}-{seconds_so_far + min(7, seconds_remaining)}).
Maintain the same energy, style, camera movement, and narrative flow. Keep the UGC authenticity.
The original video prompt was: {original_prompt}
Generate ONLY the continuation prompt. No explanations."""
        return await self._call_gpt(system, f"Generate continuation for extension #{extension_number}")

    async def generate_video_prompt(
        self,
        product_name: str,
        description: str,
        duration: int,
        format_type: str,
        creative_direction: str = "",
        product_analysis: str = "",
        buyer_persona: str = "",
    ) -> str:
        aspect_ratio = ASPECT_RATIOS.get(format_type, "9:16")
        user_msg = VIDEO_USER.format(
            product_name=product_name,
            description=description,
            duration=duration,
            format=format_type,
            aspect_ratio=aspect_ratio,
            creative_direction=creative_direction or "Auto-generate creative direction",
            product_analysis=product_analysis or "Not available",
            buyer_persona=buyer_persona or "Not available",
        )
        return await self._call_gpt(VIDEO_SYSTEM, user_msg)

    async def compress_for_veo(self, long_prompt: str) -> str:
        """Compress a detailed video script into a short VEO 3.1-compatible prompt (<500 chars)."""
        system = """You compress detailed video scripts into short VEO 3.1 prompts.
Rules:
- Output MUST be under 400 characters total
- Describe the scene visually: who, what, where, action, camera style
- Include lighting, mood, and product placement
- Use present tense, descriptive language
- NO frame-by-frame breakdown, NO timestamps, NO audio descriptions
- Output ONLY the compressed prompt, nothing else"""
        return await self._call_gpt(system, f"Compress this into a short VEO prompt:\n\n{long_prompt}")

    async def generate_image_prompt(
        self,
        product_name: str,
        description: str,
        aspect_ratio: str = "1:1",
        creative_direction: str = "",
    ) -> str:
        user_msg = IMAGE_USER.format(
            product_name=product_name,
            description=description,
            aspect_ratio=aspect_ratio,
            creative_direction=creative_direction or "Auto-generate creative direction",
        )
        return await self._call_gpt(IMAGE_SYSTEM, user_msg)

    async def generate_landing_page(
        self,
        product_name: str,
        description: str,
        image_url: str,
        style_preference: str = "",
        product_analysis: str = "",
        buyer_persona: str = "",
        extra_image_urls: list[str] | None = None,
    ) -> str:
        user_msg = LANDING_USER.format(
            product_name=product_name,
            description=description,
            image_url=image_url,
            style_preference=style_preference or "Modern, clean, professional",
            product_analysis=product_analysis or "Not available",
            buyer_persona=buyer_persona or "Not available",
            extra_images="\n".join(extra_image_urls) if extra_image_urls else "None",
        )
        return await self._call_gpt(LANDING_SYSTEM, user_msg, max_tokens=16000)

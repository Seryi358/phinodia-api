import random

# Variation pools for automatic diversity
ANGLES = [
    "45-degree angle from above, looking down at the product",
    "straight-on eye-level shot, product centered",
    "low angle looking up at the product, dramatic perspective",
    "overhead flat lay, top-down bird's eye view",
    "three-quarter angle from slightly below, product prominent",
    "close-up macro shot focusing on product texture and label details",
    "side profile angle showing product depth and shape",
]

COMPOSITIONS = [
    "rule of thirds, product on the left with negative space",
    "centered product, symmetrical composition",
    "product slightly off-center with lifestyle elements around it",
    "product in foreground with blurred contextual background",
    "product placed among complementary items, styled arrangement",
    "minimalist single product, generous white space",
    "product held in hand, showing scale and real use",
]

SURFACES = [
    "marble countertop with subtle veining",
    "rustic reclaimed wood table, warm texture",
    "clean white surface with soft shadows",
    "linen fabric draped naturally, organic feel",
    "concrete surface with industrial aesthetic",
    "bathroom shelf with everyday items nearby",
    "kitchen counter with morning light",
    "bedside table with soft lamp light",
]

LIGHTING = [
    "soft natural window light from the left side",
    "golden hour warm directional sunlight",
    "overcast diffused even lighting, no harsh shadows",
    "rim lighting from behind, creating product silhouette glow",
    "overhead soft studio light, minimal shadows",
    "warm morning light through sheer curtains",
    "cool blue-toned natural daylight",
]

UGC_SETTINGS = [
    "bathroom mirror selfie, messy counter visible, toothbrush in background",
    "bedroom with unmade bed in background, natural window light",
    "kitchen counter with coffee mug and phone charger nearby",
    "car interior, sitting in driver seat, natural daylight through windshield",
    "living room couch, TV remote and blanket visible",
    "office desk with laptop and water bottle in background",
    "outdoor cafe table with sunlight and slight shadows",
]

UGC_IMPERFECTIONS = [
    "slight motion blur on edges, phone camera quality",
    "slightly overexposed highlights from window light",
    "soft grain and noise typical of phone camera in indoor lighting",
    "autofocus slightly hunting, product sharp but background soft",
    "slight lens flare from light source, unedited look",
    "natural skin imperfections visible, no retouching",
    "uneven lighting with visible shadows, real phone photo",
]

def get_variation_context():
    """Generate random variation for each image to ensure diversity."""
    return {
        "angle": random.choice(ANGLES),
        "composition": random.choice(COMPOSITIONS),
        "surface": random.choice(SURFACES),
        "lighting": random.choice(LIGHTING),
    }

def get_ugc_variation():
    """Generate random UGC-specific variation."""
    return {
        "setting": random.choice(UGC_SETTINGS),
        "imperfection": random.choice(UGC_IMPERFECTIONS),
    }


SYSTEM_PROMPT = """<identity>
You are an expert prompt engineer for Nano Banana 2, an AI image generation model. You create concise, descriptive prompts that produce stunning product photography.
</identity>

<instructions>
Generate a SHORT, DESCRIPTIVE prompt for Nano Banana 2. The prompt will be sent DIRECTLY to the image generation model.

CRITICAL RULES:
1. ABSOLUTELY NO TEXT in the generated image — no labels, brand names, titles, watermarks, or text of any kind
2. Prompt must be UNDER 300 characters for optimal Nano Banana 2 results
3. Describe the SCENE, not the product details — the product image is provided as reference
4. Focus on: lighting, composition, background, mood, camera angle
5. The product will be placed naturally in the scene you describe
6. USE THE PROVIDED VARIATION CONTEXT — this ensures each generation is unique with different angles, compositions, and settings
</instructions>

<nano_banana_2_techniques>
EFFECTIVE KEYWORDS:
- Lighting: "soft natural window light", "golden hour warmth", "diffused overhead light", "rim lighting"
- Composition: "rule of thirds", "shallow depth of field", "centered product", "overhead flat lay"
- Surfaces: "marble countertop", "rustic wood table", "linen fabric", "concrete surface"
- Atmosphere: "minimal clean", "cozy warm tones", "fresh natural", "editorial style"
- Camera: "45-degree angle", "eye-level shot", "macro close-up", "lifestyle context"

AVOID:
- Long paragraphs — keep it concise
- Technical jargon the model won't understand
- Requesting text or typography in the image
- Over-describing the product itself (the reference image handles that)
- Words like "4K", "ultra HD", "photorealistic" (model already generates high quality)
</nano_banana_2_techniques>

<output_format>
Respond with ONLY the prompt text. No explanations, no markdown, no prefixes.
One concise paragraph, under 300 characters.
</output_format>"""

USER_TEMPLATE = """Product: {product_name}
Description: {description}
Format: {aspect_ratio}
Creative direction: {creative_direction}

VARIATION CONTEXT (use these to make this image UNIQUE):
- Camera angle: {angle}
- Composition: {composition}
- Surface/setting: {surface}
- Lighting: {lighting}

Generate a Nano Banana 2 prompt. NO TEXT in the image. Under 300 characters. Use the variation context above to make this image different from previous generations."""

USER_TEMPLATE_UGC = """Product: {product_name}
Description: {description}
Format: {aspect_ratio}
Creative direction: {creative_direction}

UGC VARIATION CONTEXT (use these for authentic imperfect look):
- Setting: {setting}
- Imperfection: {imperfection}
- Camera angle: {angle}

IMPORTANT: This must look like a REAL phone photo taken by a regular person, NOT a professional studio photo. Include visual imperfections: grain, slight blur, uneven lighting, phone camera quality. The person should look like a real customer, not a model. No retouching, no perfect lighting, no studio setup.

Generate a Nano Banana 2 prompt. NO TEXT in the image. Under 300 characters. Must look like authentic UGC content, not AI-generated."""

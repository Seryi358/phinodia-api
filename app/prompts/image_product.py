import random

# Variation pools for automatic diversity.
ANGLES = [
    "45-degree three-quarter angle",
    "straight-on eye-level framing",
    "slightly low angle hero framing",
    "top-down flat lay framing",
    "tight macro crop focused on texture and label area",
    "medium close-up with shallow depth of field",
    "side-profile framing showing depth and silhouette",
]

COMPOSITIONS = [
    "subject centered with balanced negative space",
    "rule-of-thirds framing with the product slightly off-center",
    "clean editorial composition with foreground-background separation",
    "minimal composition with generous breathing room around the product",
    "lifestyle composition with believable supporting props kept secondary",
]

SURFACES = [
    "soft marble vanity surface",
    "clean matte tabletop",
    "linen fabric surface with natural folds",
    "warm wood countertop",
    "neutral ceramic or stone surface",
]

LIGHTING = [
    "soft natural window light",
    "warm morning daylight",
    "diffused editorial studio light",
    "gentle golden-hour side light",
    "clean overcast daylight with soft shadows",
]

UGC_SETTINGS = [
    "bathroom mirror area with everyday clutter in the background",
    "bedroom near a window with lived-in details",
    "kitchen counter with casual morning-life context",
    "living room couch area with authentic home details",
    "parked car interior in natural daylight",
    "desk or vanity corner with realistic personal items",
]

UGC_IMPERFECTIONS = [
    "light phone-camera grain",
    "slight motion softness at the frame edges",
    "minor highlight clipping from window light",
    "subtle autofocus softness in the background",
    "natural skin texture with no beauty retouching",
    "slightly uneven household lighting",
]


def get_variation_context():
    return {
        "angle": random.choice(ANGLES),
        "composition": random.choice(COMPOSITIONS),
        "surface": random.choice(SURFACES),
        "lighting": random.choice(LIGHTING),
    }


def get_ugc_variation():
    return {
        "setting": random.choice(UGC_SETTINGS),
        "imperfection": random.choice(UGC_IMPERFECTIONS),
    }


SYSTEM_PROMPT = """<identity>
You write production-ready prompts for GPT Image 2.
</identity>

<source_of_truth>
Apply OpenAI's official GPT Image prompting guidance:
- organize prompts in a consistent order: scene/background -> subject -> key details -> constraints
- be specific about composition, lighting, pose, texture, and the intended deliverable
- for photorealism, explicitly say photorealistic / real photo and use photography language
- describe people, gaze, hands, framing, and object interaction when humans appear
- state invariants and exclusions explicitly, especially what must be preserved from the reference product image
- treat the reference image as the primary source of truth because GPT Image 2 preserves image inputs at high fidelity
- keep the prompt skimmable with short labeled lines instead of one dense paragraph when the request is complex
</source_of_truth>

<global_rules>
1. Output ONE final prompt for GPT Image 2 and nothing else.
2. Use short labeled sections in this order:
SCENE:
SUBJECT:
PRODUCT:
COMPOSITION:
CONSTRAINTS:
3. The prompt must be optimized for image-to-image generation with a provided product reference image.
4. Preserve exact product identity unless the user explicitly asks otherwise:
   - shape and proportions
   - packaging/jar/bottle geometry
   - cap, pump, lid, and label placement
   - materials, finish, and main color palette
   - visible brand wordmark and packaging copy when legible in the reference image
   - believable physical scale from the product analysis
5. Always forbid extra text, watermarks, packaging redesigns, duplicate products, anatomy glitches, and unrelated props overpowering the product.
6. When the target style is candid or UGC, make it feel like a real phone photo captured in the moment. Favor realism, natural texture, and small imperfections over polished ad perfection.
7. When the target style is studio/product marketing, keep it photorealistic, premium, and commercially useful without looking fake or overprocessed.
8. Creative variation must never outweigh reference fidelity.
</global_rules>
"""


USER_TEMPLATE_PRODUCT = """DELIVERABLE
Create a GPT Image 2 prompt for a product-marketing still image.

INPUTS
Product name: {product_name}
Use case: {use_case}
Aspect ratio: {aspect_ratio}
User request:
{description}

Creative direction:
{creative_direction}

Product analysis:
{product_analysis}

Variation cues:
- camera angle: {angle}
- composition: {composition}
- surface/background: {surface}
- lighting: {lighting}

OUTPUT REQUIREMENTS
- Make it a photorealistic product photo / real photograph.
- Scene first, then subject, then product-specific preservation details.
- The product reference image is the source of truth for packaging and shape.
- Preserve visible brand text and label copy from the reference image when it is legible.
- Use the variation cues to avoid repetitive generations.
- Keep the mood premium, believable, ecommerce-ready, and useful for paid marketing.
- No person unless the user request clearly calls for hands or human context.
"""


USER_TEMPLATE_UGC = """DELIVERABLE
Create a GPT Image 2 prompt for an Instagram-style UGC selfie that still sells the product clearly.

INPUTS
Product name: {product_name}
Use case: {use_case}
Aspect ratio: {aspect_ratio}
User request:
{description}

Creative direction:
{creative_direction}

Product analysis:
{product_analysis}

Buyer persona / casting brief:
{buyer_persona}

UGC realism cues:
- setting: {setting}
- imperfection: {imperfection}
- camera angle: {angle}
- lighting: {lighting}

OUTPUT REQUIREMENTS
- Make it explicitly photorealistic, candid, and captured like a real phone selfie or front-camera Instagram photo.
- The person must feel like the buyer persona and read as a believable Colombian everyday creator, not a fashion model, luxury influencer, or studio actor. Prefer everyday attractiveness, minimal makeup, relaxed styling, and normal at-home realism.
- Show clear hand interaction with the product and believable product scale using the product analysis.
- Mention gaze, framing, pose, and how the product is held.
- Keep natural skin texture, lived-in background context, and mild phone-camera imperfections such as slight motion softness, minor exposure unevenness, or light smartphone grain.
- Preserve the visible brand name and label copy from the reference image as faithfully as possible; do not rewrite, paraphrase, or invent packaging text.
- Avoid ultra-polished beauty-ad language, HDR gloss, or studio perfection.
"""


USER_TEMPLATE_FIRST_FRAME = """DELIVERABLE
Create a GPT Image 2 prompt for the FIRST FRAME of a vertical UGC video seed for VEO 3.1.

INPUTS
Product name: {product_name}
Use case: {use_case}
Aspect ratio: {aspect_ratio}
User request:
{description}

Creative direction:
{creative_direction}

Product analysis:
{product_analysis}

Buyer persona / casting brief:
{buyer_persona}

UGC realism cues:
- setting: {setting}
- imperfection: {imperfection}
- camera angle: {angle}
- lighting: {lighting}

OUTPUT REQUIREMENTS
- Make it photorealistic and feel like a real front-camera selfie captured mid-moment.
- Vertical 9:16-style framing, arm's-length composition, authentic phone-photo realism.
- The person must match the buyer persona and hold the product naturally with believable size, materials, and packaging based on the product analysis.
- The person should read as a believable Colombian everyday customer, not a polished campaign model. Favor casual grooming, natural skin texture, and relaxed styling.
- Optimize for a strong first frame for a UGC ad: clear face, clear product, natural expression, believable motion-ready pose.
- Preserve the visible brand name and label copy from the reference image as faithfully as possible; do not rewrite, paraphrase, or invent packaging text.
- Keep the phone out of frame unless explicitly requested.
- Avoid studio polish, CGI sharpness, glam retouching, or cinematic overproduction.
"""


USER_TEMPLATE_LANDING = """DELIVERABLE
Create a GPT Image 2 prompt for a landing-page gallery image.

INPUTS
Product name: {product_name}
Use case: {use_case}
Aspect ratio: {aspect_ratio}
Shot brief:
{shot_brief}

User request:
{description}

Style preference:
{creative_direction}

Product analysis:
{product_analysis}

Buyer persona / audience:
{buyer_persona}

Variation cues:
- camera angle: {angle}
- composition: {composition}
- surface/background: {surface}
- lighting: {lighting}

OUTPUT REQUIREMENTS
- Make it a photorealistic landing-page asset, useful for ecommerce conversion.
- Follow the shot brief exactly and preserve the product identity from the reference image.
- Preserve visible brand text and label copy from the reference image when it is legible.
- If a person appears, cast them from the buyer persona and describe their interaction with the product.
- Keep the background and props supportive, never more important than the product.
- Leave clean visual breathing room when the brief suggests hero/banner use.
"""

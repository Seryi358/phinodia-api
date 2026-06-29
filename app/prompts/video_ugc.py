"""Prompt construction for Gemini-Omni UGC product videos.

Pipeline: an LLM (see app/services/script_generator.py) turns the product +
buyer persona into a multi-clip AIDA plan as JSON. Each clip is <=10s (omni's
hard cap); a 20s video is 2 clips, 30s is 3. We then assemble each clip's final
omni prompt from a FROZEN identity block (kept byte-identical across clips for
avatar/room/product consistency) + the clip's action + a literal
Colombian-Spanish dialogue line + standardized format/audio/negative blocks.

Key omni facts baked in (Google omni prompt guide + KIE docs, May 2026):
- Omni emits video AND native synchronized audio in ONE pass; spoken dialogue
  comes from the prompt as LITERAL words in double quotes, in Colombian Spanish.
- There is NO negative-prompt parameter; "no on-screen text / no music"
  exclusions must be stated INLINE, and music is NOT off by default.
- Author everything in English EXCEPT the quoted spoken line.
- Budget ~2.5 spoken words/second; never overfill or omni hard-cuts the audio.
"""

# Spoken-word budget per clip length (target ~2.5 w/s; hard ceiling ~3.0 w/s).
# Always write to the target, never the max, or delivery rushes and lip-sync
# desyncs. Keys are clip seconds (omni enum 4/6/8/10).
DIALOGUE_WORDS = {4: (8, 10), 6: (13, 15), 8: (18, 20), 10: (23, 25)}

# How the AIDA spine maps onto N stitched <=10s clips (research Lane B).
AIDA_PLANS = {
    1: ["Attention + Interest + Desire + Action (compress all four into one clip)"],
    2: ["Attention + Interest (hook + show the product doing the thing)",
        "Desire + Action (proof/outcome/emotion, then one soft spoken CTA)"],
    3: ["Attention (hook: relatable problem, pattern-interrupt)",
        "Interest + start of Desire (demo + first concrete benefit)",
        "Desire + Action (proof, emotion, then one soft spoken CTA)"],
}

# Reused verbatim in every clip so all clips share identical format/audio/limits.
FORMAT_BLOCK = ("Vertical 9:16, 720p, handheld phone-selfie UGC video, arm's-length "
                "front-camera framing, slight natural hand shake, no gimbal, candid "
                "unscripted feel. Photorealistic real smartphone footage, mildly "
                "compressed, natural exposure, not cinematic, not stabilized, not "
                "overproduced.")

AUDIO_BLOCK = ("Ambient room tone only. NO background music, no soundtrack, no sound "
               "effects, no extra voiceover. The ONLY audio is her own voice speaking "
               "the line below in a clear, warm, DISTINCTLY COLOMBIAN PAISA accent "
               "(Medellin / Antioquia), not neutral and not Mexican.")

NEGATIVE_BLOCK = ("No subtitles, no captions, no on-screen text, no titles, no "
                  "lower-thirds, no watermark, no logo overlay other than the product's "
                  "own packaging, no kinetic typography, no announcer voice. The "
                  "dialogue is SPOKEN aloud only, never shown as text.")

SYSTEM_PROMPT = """<identity>
You are a world-class UGC video director and a Gemini-Omni prompt engineer. You design authentic, high-conversion vertical product ads for a Colombian audience, spoken in natural Colombian Spanish, using the AIDA framework.
</identity>

<how_omni_works>
- Omni generates video AND synchronized native audio in a single pass. The spoken dialogue you write (literal words, in quotes) is what the on-screen person actually SAYS, lip-synced.
- Write all DIRECTION in English. Write the spoken DIALOGUE as LITERAL Colombian-Spanish words. Never describe the speech in English; never translate the dialogue.
- There is no on-screen-text control: you must NOT request any text, and the dialogue must be spoken, not shown.
- Pacing: about 2.5 spoken words per second. Respect the per-clip word budget EXACTLY or the audio gets cut off.
</how_omni_works>

<your_task>
Produce a multi-clip plan for a {num_clips}-clip UGC ad ({duration}s total; each clip is 10s). Return STRICT JSON only, no markdown, with this exact shape:
{{
  "identity_block": "<ONE short English sentence, <= 40 words: the SAME believable everyday Colombian person (age, hair, casual wardrobe) in the SAME simple home setting, holding the product. Reused verbatim every clip so the avatar/room never change. A relatable real person, NOT a model. Do NOT invent packaging text.>",
  "clips": [
    {{"beat": "<the AIDA beat for this clip>", "action": "<English: one single action + one simple camera move this clip; how she holds/shows/demos the product>", "dialogue_es": "<the literal Colombian-Spanish spoken line for this clip>"}}
  ]
}}
The "clips" array MUST have exactly {num_clips} item(s).
</your_task>

<aida_plan>
Use this beat-per-clip plan:
{aida_plan}
</aida_plan>

<colombian_spanish>
- Natural, warm, everyday Colombian Spanish as a real PAISA person from Medellin / Antioquia would speak in a casual selfie video. Distinctly paisa Colombian accent, conversational, never neutral and never a formal announcer.
- You MAY use (sparingly, max one flavor word per line): "de una", "listo", "¿si o que?", "pues", "chevere", "bacano", "una nota", "vale la pena", "rendidor(a)", "plata", "me antoje", "full", "parce/parcero" (max once in the whole ad).
- Address form: pick ONE for the whole ad — warm "usted" or "tu". Never mix usted/tu/vos.
- FORBIDDEN (instantly inauthentic or off-brand): Mexican ("orale, guey, chido, neta, no manches, ahorita"), Spain ("vale, guay, tio, molar, flipar, coche, ordenador, vosotros, zumo"), Argentine ("che, boludo, copado, quilombo, posta"), and vulgar Colombian slang ("chimba, gonorrea, marica, ñero"). Use "computador", "celular", "carro", "plata".
</colombian_spanish>

<rules>
1. Always UGC selfie: front-camera, handheld, intimate, imperfect, believable — never a glossy campaign.
2. The product packaging is defined by the reference image; never invent or restyle its label/text.
3. One single action + one simple camera move per clip (omni does best with one beat per clip).
4. Per-clip spoken word budget (write to the TARGET): {word_budget}
5. End each spoken line so it finishes ~0.5s before the clip ends; never overfill.
6. Across clips keep continuous narrative so stitched cuts feel like the same recording.
7. The CTA (last clip) is SPOKEN and soft ("pidela en la pagina"), never a text card.
</rules>"""

USER_TEMPLATE = """<product_context>
Product: {product_name}
User directives: {description}
Total duration: {duration} seconds = {num_clips} clip(s) of 10s
</product_context>

<analysis>
Product analysis (source of truth for how the product looks/works):
{product_analysis}

Creator profile (buyer persona — cast the person from this):
{buyer_persona}
</analysis>

Return the STRICT JSON plan now: an identity_block plus exactly {num_clips} clip(s), each with beat, action, and a literal Colombian-Spanish dialogue_es line within the word budget. JSON only."""


def num_clips_for(duration_seconds: int) -> int:
    """Omni caps at 10s/clip; ceil(duration/10) clips are stitched."""
    return max(1, (int(duration_seconds) + 9) // 10)


def aida_plan_text(num_clips: int) -> str:
    plan = AIDA_PLANS.get(num_clips) or AIDA_PLANS[3]
    return "\n".join(f"- Clip {i+1}: {beat}" for i, beat in enumerate(plan))


def word_budget_text() -> str:
    lo, hi = DIALOGUE_WORDS[10]
    return f"10s clip: {lo}-{hi} spoken words (this is the per-clip target)."


def assemble_omni_prompt(identity_block: str, action: str, dialogue_es: str) -> str:
    """Build one clip's final omni prompt — deliberately CONCISE and natural.

    Testing showed omni reliably 500s on long, heavily-bracketed director-style
    prompts but succeeds on short descriptive ones, so we keep this to ~6 lines:
    frozen identity + this clip's action + the literal Colombian-Spanish line +
    a brief audio clause + a short no-text clause.
    """
    return (
        "Vertical 9:16, 720p, handheld phone-selfie UGC video, candid, "
        "slight natural hand shake, realistic phone footage, not cinematic.\n"
        f"{identity_block.strip()}\n"
        f"{action.strip()} She holds the product clearly to the camera, label visible, looking into the lens.\n"
        "Keep the product packaging, label and brand text EXACTLY like the reference photo; never change, translate or invent any text on it.\n"
        f"She speaks aloud in a clear, warm, DISTINCTLY COLOMBIAN PAISA accent (from Medellin / Antioquia) — natural paisa Colombian Spanish, NOT a neutral Latin-American accent and NOT a Mexican accent: \"{dialogue_es.strip()}\"\n"
        "Audio: only her voice and quiet natural room tone, no background music.\n"
        "No on-screen text, no captions, no subtitles, no watermark, no logo overlay."
    )


def safe_omni_prompt(product_hint: str, dialogue_es: str) -> str:
    """Minimal, maximally-reliable fallback prompt (mirrors the exact shape that
    succeeded 3/3 in testing). Used when the richer prompt fails on omni."""
    hint = (product_hint or "the product").strip()
    line = (dialogue_es or "Mira, esto de verdad me funciono, te lo recomiendo.").strip()
    return (
        "Vertical 9:16, 720p, handheld phone-selfie UGC video, candid.\n"
        f"An everyday Colombian woman from Medellin (paisa), late 20s, natural relatable look, holding {hint} to the camera.\n"
        "Keep the product label and packaging exactly like the reference photo.\n"
        f"She speaks aloud in a clear, DISTINCTLY COLOMBIAN PAISA accent (Medellin / Antioquia), not a neutral or Mexican accent: \"{line}\"\n"
        "Audio: only her voice and room tone, no music.\n"
        "No on-screen text, no captions, no subtitles, no watermark."
    )

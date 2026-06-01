"""Unit tests for the new omni video logic: credit costs, clip math, job
labels and prompt assembly. The full worker (_process_video) is validated
end-to-end against the live KIE API via scripts/omni_smoke_test.py."""
from app.services.credits import video_credit_cost, credits_for_service_label, action_cost
from app.prompts.video_ugc import num_clips_for, assemble_omni_prompt, safe_omni_prompt
from app.routers.generate import _video_service


def test_video_credit_cost_is_3_per_10s():
    assert video_credit_cost(10) == 3
    assert video_credit_cost(20) == 6
    assert video_credit_cost(30) == 9


def test_num_clips_for_duration():
    assert num_clips_for(10) == 1
    assert num_clips_for(20) == 2
    assert num_clips_for(30) == 3


def test_video_service_label():
    assert _video_service(10) == "video_10s"
    assert _video_service(20) == "video_20s"
    assert _video_service(30) == "video_30s"


def test_credits_for_service_label():
    assert credits_for_service_label("video_10s") == 3
    assert credits_for_service_label("video_20s") == 6
    assert credits_for_service_label("video_30s") == 9
    assert credits_for_service_label("image") == 1
    assert credits_for_service_label("landing_page") == 6
    # Legacy labels (pre-migration jobs) still map to a fair refund amount.
    assert credits_for_service_label("video_15s") == 6  # 15s -> 2 x 10s clips


def test_action_cost():
    assert action_cost("video", 20) == 6
    assert action_cost("image") == 1
    assert action_cost("landing_page") == 6


def test_assemble_omni_prompt_spanish_dialogue_and_no_text():
    p = assemble_omni_prompt(
        "An everyday Colombian woman holding the product.",
        "She smiles and shows it to the camera.",
        "Hola, esto de verdad me encanto.")
    assert '"Hola, esto de verdad me encanto."' in p   # literal Spanish, quoted
    assert "Colombian Spanish" in p                     # accent named in English
    assert "No on-screen text" in p                     # text suppression inline
    assert "no background music" in p                   # music suppression inline
    assert len(p) < 900                                 # concise (omni fails on long prompts)


def test_safe_omni_prompt_is_short_fallback():
    p = safe_omni_prompt("Serum GlowSkin", "Mira esto, me funciono.")
    assert "Serum GlowSkin" in p
    assert '"Mira esto, me funciono.' in p
    assert "no music" in p
    assert len(p) < 600

#!/usr/bin/env python3
"""
Standalone end-to-end validation of the NEW Gemini-Omni UGC video pipeline.
Pure stdlib (urllib) + a static ffmpeg binary (imageio-ffmpeg). No app deps.

Flow (mirrors what app/services/kie_ai.py + video_stitch.py will do):
  1. upload product photo            -> public URL
  2. gpt-image-2-image-to-image      -> 9:16 selfie first frame
  3. omni/character/create           -> characterId (best-effort)
  4. gemini-omni-video clip 1 (10s)  -> mp4  [image_urls=frame]
  5. ffmpeg extract last frame       -> upload -> public URL
  6. gemini-omni-video clip 2 (10s)  -> mp4  [image_urls=lastframe, same char/seed]
  7. ffmpeg normalize + concat       -> final 20s mp4
Reports real KIE credits consumed (balance before/after).

Usage: KIE_API_KEY=... python3 scripts/omni_smoke_test.py
"""
import base64, json, os, subprocess, sys, time, urllib.parse, urllib.request

KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()
KIE = "https://api.kie.ai/api/v1"
UPLOAD_URL = "https://kieai.redpandaai.co/api/file-base64-upload"
SEED = 778899
OUT = os.path.join(os.path.dirname(__file__), "_omni_out")
os.makedirs(OUT, exist_ok=True)

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception as e:
    print("ffmpeg binary missing:", e); sys.exit(2)

def _hdr(json_body=True):
    h = {"Authorization": f"Bearer {KIE_API_KEY}"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

def http(method, url, *, headers=None, data=None, timeout=120):
    h = {"User-Agent": _UA, "Accept": "application/json, */*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, method=method, headers=h,
                                 data=data if isinstance(data, (bytes, type(None))) else data.encode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)

def kie_credit():
    s, b = http("GET", f"{KIE}/chat/credit", headers=_hdr(False))
    try:
        return json.loads(b).get("data")
    except Exception:
        return None

def log(step, msg): print(f"[{step}] {msg}", flush=True)

def upload(local_path, name):
    raw = open(local_path, "rb").read()
    ext = "png" if local_path.lower().endswith(".png") else "jpeg"
    b64 = base64.b64encode(raw).decode()
    body = json.dumps({
        "base64Data": f"data:image/{ext};base64,{b64}",
        "uploadPath": "phinodia/test",
        "fileName": name,
    })
    s, b = http("POST", UPLOAD_URL,
                headers={"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"},
                data=body)
    log("upload", f"HTTP {s} {b[:160]}")
    d = json.loads(b)
    return (d.get("data") or {}).get("downloadUrl") or (d.get("data") or {}).get("url")

def create_task(model, inp):
    body = json.dumps({"model": model, "input": inp})
    s, b = http("POST", f"{KIE}/jobs/createTask", headers=_hdr(), data=body)
    if s != 200:
        log("createTask", f"HTTP {s} {b[:300]}"); return None
    d = json.loads(b)
    if d.get("code") not in (200, "200"):
        log("createTask", f"code={d.get('code')} msg={d.get('msg')}"); return None
    return (d.get("data") or {}).get("taskId")

def poll(task_id, label, max_polls=120, interval=5):
    for i in range(max_polls):
        s, b = http("GET", f"{KIE}/jobs/recordInfo?taskId={task_id}", headers=_hdr(False))
        try:
            data = (json.loads(b) or {}).get("data") or {}
        except Exception:
            data = {}
        state = data.get("state", "?")
        if i % 4 == 0 or state in ("success", "fail", "failed"):
            log(label, f"poll#{i} state={state} progress={data.get('progress')}")
        if state == "success":
            rj = data.get("resultJson")
            urls = []
            if rj:
                try: urls = json.loads(rj).get("resultUrls") or json.loads(rj).get("urls") or []
                except Exception: pass
            return {"ok": True, "url": urls[0] if urls else None,
                    "credits": data.get("creditsConsumed") or data.get("costCredits"), "raw": data}
        if state in ("fail", "failed"):
            return {"ok": False, "err": f"{data.get('failCode')}:{data.get('failMsg')}", "raw": data}
        time.sleep(interval)
    return {"ok": False, "err": "timeout"}

def ff(args):
    r = subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error"] + args,
                       capture_output=True, text=True)
    if r.returncode != 0:
        log("ffmpeg", "ERR " + r.stderr[:300])
    return r.returncode == 0

def duration(path):
    r = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "Duration" in line:
            return line.strip().split(",")[0]
    return "?"

def download(url, path):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=180) as r:
        open(path, "wb").write(r.read())
    return os.path.getsize(path)

# ---- prompts (research-based: English direction + quoted Colombian Spanish) ----
FIRST_FRAME_PROMPT = """USE: First frame for a vertical 9:16 UGC selfie product-ad video.
SCENE: Everyday Colombian home interior, natural daylight from a nearby window, lived-in and modest, no studio.
PERSON: An ordinary everyday Colombian woman in her late 20s, relatable and casual, NOT a fashion model. Natural hair, casual everyday clothes, warm genuine half-smile, looking straight into the front camera. Visible real skin texture: pores, slight under-eye shadows, a few flyaway hairs, natural uneven skin tone.
PRODUCT: She is holding THIS EXACT PRODUCT (the reference image) at chest height, label fully facing the camera. Preserve the product geometry, packaging, label, all printed text, logo, colors and proportions IDENTICAL to the reference; do not redraw or restyle the label. Natural fully-formed hand grip with a subtle realistic contact shadow.
CAMERA/LOOK: Candid front-camera smartphone selfie held at arm's length, vertical 9:16. Soft phone front-camera lens, mild sensor noise and grain, slightly flat smartphone dynamic range, natural indoor light, minor handheld softness. Photorealistic, honest and unposed. NOT cinematic, NOT a professional photoshoot, no studio lighting, no beauty retouch.
EXCLUSIONS: no on-screen text, no captions or subtitles, no watermark, no logos other than the product's own, no UI overlays, no extra props, exactly ONE product visible, no duplicate products, hands fully formed."""

def omni_prompt(spanish_line, action):
    return f"""[FORMAT] Vertical 9:16, 720p, handheld phone-selfie UGC video, arm's-length front-camera framing, slight natural hand shake, no gimbal, candid unscripted feel.
[PERSON] The same everyday Colombian woman from the reference image, late 20s, relatable casual style, natural look. Keep the same person, wardrobe and setting.
[ACTION + PRODUCT] {action} She clearly holds the product at chest height, label visible and clean, looking directly into the camera.
[DIALOGUE - spoken aloud, in clear neutral Colombian Spanish, conversational and warm]:
  "{spanish_line}"
[SETTING] Bright everyday Colombian home, natural window daylight.
[VIBE] Warm natural light, realistic skin, slightly soft phone-camera look, relaxed candid pacing.
[AUDIO] Ambient room tone only. No background music. The only audio is her voice speaking Colombian Spanish.
[NEGATIVE] No subtitles, no captions, no on-screen text, no titles, no lower-thirds, no watermark, no logo overlay, no kinetic typography, no announcer voice. Dialogue is spoken only."""

def main():
    if not KIE_API_KEY:
        print("Set KIE_API_KEY"); sys.exit(2)
    bal0 = kie_credit()
    log("balance", f"KIE credits BEFORE = {bal0}")

    src = os.path.join(os.path.dirname(__file__), "..", "frontend", "static", "images", "demo-producto.jpg")
    src = os.path.abspath(src)
    log("step1", f"uploading product photo {src}")
    product_url = upload(src, "product.jpg")
    if not product_url: log("step1", "UPLOAD FAILED"); sys.exit(1)
    log("step1", f"product_url = {product_url}")

    log("step2", "gpt-image-2 first frame (9:16)")
    tid = create_task("gpt-image-2-image-to-image", {
        "prompt": FIRST_FRAME_PROMPT, "input_urls": [product_url],
        "aspect_ratio": "9:16", "resolution": "1K"})
    if not tid: log("step2", "createTask FAILED"); sys.exit(1)
    r = poll(tid, "step2-frame", max_polls=60)
    if not r["ok"] or not r["url"]: log("step2", f"FAILED {r.get('err')}"); sys.exit(1)
    frame_url = r["url"]
    log("step2", f"first_frame = {frame_url} (credits={r.get('credits')})")

    # character_ids DISABLED — it likely caused the earlier 500. Consistency
    # comes from the gpt-image-2 first frame + last-frame relay + fixed seed,
    # which is the documented backbone anyway.
    char_id = None
    log("step3", "character creation skipped (frame relay + fixed seed)")

    def omni_clip(n, image_url, spanish, action):
        inp = {"prompt": omni_prompt(spanish, action), "image_urls": [image_url],
               "duration": "10", "aspect_ratio": "9:16", "resolution": "720p", "seed": SEED}
        if char_id: inp["character_ids"] = [char_id]
        tid = create_task("gemini-omni-video", inp)
        if not tid: return None
        return poll(tid, f"clip{n}", max_polls=120)

    log("step4", "omni clip 1 (10s, first frame)")
    r1 = omni_clip(1, frame_url,
                   "Parce, llevaba meses buscando algo que de verdad funcionara, y mire, esto me cambio la rutina por completo.",
                   "She shows the product to the camera, excited, like recommending it to a friend.")
    if not r1 or not r1["ok"] or not r1["url"]: log("step4", f"FAILED {r1 and r1.get('err')}"); sys.exit(1)
    clip1 = os.path.join(OUT, "clip1.mp4"); download(r1["url"], clip1)
    log("step4", f"clip1 saved {os.path.getsize(clip1)}B dur={duration(clip1)} credits={r1.get('credits')}")

    log("step5", "extract last frame of clip1")
    lastframe = os.path.join(OUT, "lastframe.png")
    ff(["-sseof", "-0.3", "-i", clip1, "-update", "1", "-frames:v", "1", "-q:v", "2", lastframe])
    lf_url = upload(lastframe, "lastframe.jpg") if os.path.exists(lastframe) else None
    log("step5", f"lastframe uploaded = {lf_url}")

    log("step6", "omni clip 2 (10s, last-frame relay)")
    r2 = omni_clip(2, lf_url or frame_url,
                   "En serio quede feliz con el resultado. Si quiere la suya, pidala ya en la pagina, de una, sí o qué.",
                   "She smiles warmly, shows the result, and gives a soft call to action.")
    if not r2 or not r2["ok"] or not r2["url"]: log("step6", f"FAILED {r2 and r2.get('err')}"); sys.exit(1)
    clip2 = os.path.join(OUT, "clip2.mp4"); download(r2["url"], clip2)
    log("step6", f"clip2 saved {os.path.getsize(clip2)}B dur={duration(clip2)} credits={r2.get('credits')}")

    log("step7", "normalize + concat -> final 20s")
    norm = []
    for i, c in enumerate([clip1, clip2], 1):
        n = os.path.join(OUT, f"norm{i}.mp4")
        ok = ff(["-i", c, "-vf",
                 "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "aac", "-ar", "48000", "-b:a", "128k", n])
        if ok: norm.append(n)
    listf = os.path.join(OUT, "list.txt")
    open(listf, "w").write("".join(f"file '{p}'\n" for p in norm))
    final = os.path.join(OUT, "final_20s.mp4")
    ff(["-f", "concat", "-safe", "0", "-i", listf, "-c", "copy", final])
    log("step7", f"FINAL = {final} dur={duration(final)} size={os.path.getsize(final) if os.path.exists(final) else 0}B")

    bal1 = kie_credit()
    spent = (bal0 - bal1) if (bal0 is not None and bal1 is not None) else "?"
    print("\n==================== RESULTADO ====================")
    print(f"  KIE credits: {bal0} -> {bal1}  (gastados: {spent} ≈ ${(spent*0.005):.3f})" if isinstance(spent,(int,float)) else f"  credits {bal0}->{bal1}")
    print(f"  primer frame: {frame_url}")
    print(f"  characterId : {char_id}")
    print(f"  clip1: {r1.get('credits')} cr | clip2: {r2.get('credits')} cr")
    print(f"  VIDEO FINAL : {final}  ({duration(final)})")
    print("===================================================")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""One-time migration: per-service `credits` table  ->  unified `users.credits`.

The old model stored balances per service_type (video_8s/15s/22s/30s, image,
landing_page) in the `credits` table. The new model uses a SINGLE wallet in
users.credits where actions cost a number of credits (video 3/10s, image 1,
landing 6). This script converts each user's remaining per-service credits to a
fair unified balance so nobody loses value on cutover.

Conversion (generous — each old credit is worth the new cost of the same/closest
action): video_8s->3, video_15s->6, video_22s->9, video_30s->9, image->1,
landing_page->6.

Idempotent: only writes users whose users.credits is currently 0 (so re-runs and
post-deploy purchases are never double-counted).

Usage:
  python3 scripts/migrate_credits_unified.py            # DRY RUN (prints plan)
  python3 scripts/migrate_credits_unified.py --apply    # actually writes
"""
import json, os, sys, urllib.request, urllib.error

SB = os.environ.get("SUPABASE_URL", "https://bxeiecdxryelwrtcwupe.supabase.co").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
APPLY = "--apply" in sys.argv

CONV = {
    "video_8s": 3, "video_10s": 3, "video_15s": 6, "video_20s": 6,
    "video_22s": 9, "video_30s": 9, "image": 1, "landing_page": 6,
}


def req(method, path, body=None):
    url = f"{SB}/rest/v1/{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, method=method, data=data, headers={
        "apikey": KEY, "Authorization": f"Bearer {KEY}",
        "Content-Type": "application/json", "Prefer": "return=representation",
    })
    with urllib.request.urlopen(r, timeout=30) as resp:
        txt = resp.read().decode()
        return json.loads(txt) if txt else []


def main():
    if not KEY:
        print("Set SUPABASE_SERVICE_KEY"); sys.exit(2)
    users = req("GET", "users?select=id,email,credits&limit=1000")
    credits = req("GET", "credits?select=user_id,service_type,total,used&limit=5000")
    by_user = {}
    for c in credits:
        by_user.setdefault(c["user_id"], []).append(c)

    print(f"{'email':38s} {'old(by service)':30s} -> new  (current users.credits)")
    print("-" * 92)
    plan = []
    unknown = set()
    for u in users:
        rows = by_user.get(u["id"], [])
        total_new = 0
        parts = []
        for r in rows:
            avail = max(0, int(r.get("total", 0)) - int(r.get("used", 0)))
            if avail <= 0:
                continue
            st = r.get("service_type", "")
            if st not in CONV:
                unknown.add(st)
            factor = CONV.get(st, 1)
            total_new += avail * factor
            parts.append(f"{st}:{avail}x{factor}")
        cur = int(u.get("credits") or 0)
        will_write = total_new > 0 and cur == 0
        if total_new > 0 or cur > 0:
            flag = "WRITE" if will_write else ("skip(cur!=0)" if cur != 0 else "skip(0)")
            email = (u.get("email") or "")[:36]
            print(f"{email:38s} {' '.join(parts)[:30]:30s} -> {total_new:<4d} (cur={cur}) {flag}")
        if will_write:
            plan.append((u["id"], total_new))

    print("-" * 92)
    print(f"Usuarios a migrar: {len(plan)} | creditos unificados a otorgar: {sum(p[1] for p in plan)}")
    if unknown:
        print(f"⚠️  service_types sin conversion (usados x1): {sorted(unknown)}")

    if not APPLY:
        print("\nDRY RUN — nada escrito. Re-ejecuta con --apply para aplicar.")
        return
    print("\nAPLICANDO...")
    ok = 0
    for uid, amount in plan:
        try:
            req("PATCH", f"users?id=eq.{uid}&credits=eq.0", {"credits": amount})
            ok += 1
        except urllib.error.HTTPError as e:
            print(f"  error user {uid}: {e} {e.read().decode()[:120]}")
    print(f"✅ {ok}/{len(plan)} usuarios migrados.")


if __name__ == "__main__":
    main()

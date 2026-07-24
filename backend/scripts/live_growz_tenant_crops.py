"""Live probe: a farmer's REAL Growz profile crops via the tenant OTP API.

Verifies the data path we need for the "profile crops next to «Ekinlar»"
feature BEFORE any UI is built. The Growz farmer login is OTP-based:

    POST /api/tenant/auth/sign-in  {phone, countryCode}
        -> {"message":"OTP sent...","code":"NNNNN", "telegramUrl":...}
        (the code is echoed in the response on this environment)
    POST /api/tenant/auth/sign-in  {phone, countryCode, code, otpCode:false}
        -> Bearer token          <-- CURRENTLY 500s on a CORRECT code
    GET  /api/tenant/crops        Authorization: Bearer <token>  -> real crops

KNOWN BLOCKER (2026-07, §1.2): the verify call returns 500 Internal Server
Error whenever the code is *correct* (a WRONG code returns a clean
`error.OTP_INCORRECT` 400). i.e. the endpoint matches the OTP, then crashes
before returning a token. This is a Growz backend bug — until it's fixed we
cannot obtain a tenant token, so `/api/tenant/crops` stays unreachable.

Run (code auto-read from the send response on this env; or pass one):

    GROWZ_TENANT_PHONE=+998XXXXXXXXX \
    [GROWZ_COUNTRY_CODE=uz] [GROWZ_OTP_CODE=NNNNN] \
    GROWZ_API_KEY=... \
    PYTHONPATH=. python scripts/live_growz_tenant_crops.py
"""
import json
import os

import httpx

from app.config import Settings

_SIGN_IN = "/api/tenant/auth/sign-in"
# The farmer's real plants list. /plantings is the CRUD resource the "Ўсимлик
# qo'shish" screen writes to; /crops and /fields are the related tenant resources.
_PLANTINGS = "/api/tenant/plantings"
_PLANTINGS_CURRENT = "/api/tenant/plantings/current"
_CROPS = "/api/tenant/crops"
_FIELDS = "/api/tenant/fields"


def _headers(settings: Settings, token: str | None = None) -> dict:
    h = {"Content-Type": "application/json", "x-api-key": settings.growz_api_key}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _extract_token(body) -> str | None:
    if not isinstance(body, dict):
        return None
    for k in ("accessToken", "access_token", "token", "jwt"):
        if isinstance(body.get(k), str):
            return body[k]
    return _extract_token(body.get("data"))


def main() -> None:
    settings = Settings()
    phone = os.environ.get("GROWZ_TENANT_PHONE", "").strip()
    cc = os.environ.get("GROWZ_COUNTRY_CODE", "uz").strip() or "uz"
    if not (phone and settings.growz_api_key):
        raise SystemExit("Set GROWZ_TENANT_PHONE and GROWZ_API_KEY.")

    with httpx.Client(timeout=20) as client:
        code = os.environ.get("GROWZ_OTP_CODE", "").strip()
        if not code:
            r = client.post(
                f"{settings.growz_api_url}{_SIGN_IN}",
                headers=_headers(settings),
                json={"phone": phone, "countryCode": cc},
            )
            body = r.json() if r.headers.get("content-type", "").startswith(
                "application/json"
            ) else {"_raw": r.text[:200]}
            print(f"send OTP -> http {r.status_code}: {body}")
            code = str(body.get("code") or "")
            if not code:
                print("No code echoed — pass GROWZ_OTP_CODE from the SMS/Telegram.")
                return

        # verify — otpCode:false is the handled verify path (true 500s harder).
        r = client.post(
            f"{settings.growz_api_url}{_SIGN_IN}",
            headers=_headers(settings),
            json={"phone": phone, "countryCode": cc, "code": code, "otpCode": False},
        )
        try:
            vbody = r.json()
        except Exception:  # noqa: BLE001
            vbody = {"_raw": r.text[:300]}
        print(f"\nverify -> http {r.status_code}: {vbody}")

        token = _extract_token(vbody)
        if not token:
            if r.status_code == 500:
                print(
                    "\n⛔ BLOCKED: verify 500s on a correct code — the Growz "
                    "backend crashes after matching the OTP (§1.2 blocker). "
                    "Growz must fix POST /api/tenant/auth/sign-in verify before "
                    "we can read the farmer's real crops."
                )
            return

        print(f"\n✅ token obtained (len={len(token)})")
        for path in (_PLANTINGS, _PLANTINGS_CURRENT, _CROPS, _FIELDS):
            r = client.get(
                f"{settings.growz_api_url}{path}", headers=_headers(settings, token)
            )
            print(f"\n=== GET {path} -> http {r.status_code} ===")
            try:
                data = r.json()
            except Exception:  # noqa: BLE001
                print(r.text[:400]); continue
            payload = data.get("data", data) if isinstance(data, dict) else data
            n = len(payload) if isinstance(payload, list) else "?"
            print(f"count: {n}")
            print(json.dumps(payload, ensure_ascii=False, indent=2)[:1500])


if __name__ == "__main__":
    main()

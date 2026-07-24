# Growz `/tenant/*` API — findings (Phase 0)

Reverse-engineered from the production Flutter app (`uz.consort.growz` v3.0.6) + live
probing against `https://v2-api.growz.io` (prod). Source of truth for the Alomat↔Growz
integration. "CONFIRMED" = observed via a live request; "INFERRED" = from APK strings only.

## Base URL & prefix

- Prod: `https://v2-api.growz.io`
- Dev/staging: `https://dev2-api.growz.io`
- **All mobile-app routes are under `/api/tenant/...`** (CONFIRMED — the raw `/tenant/...` from
  the binary is missing the `/api` prefix; `/api/tenant/auth/sign-in` exists, `/tenant/auth/sign-in` 404s).
- The AI read-only surface is under `/api/ai/...` and needs a different auth (`token header required`
  → an `x-api-key`/token header, NOT the tenant bearer).
- Public, no-auth: `/api/public/countries`.

## Auth (CONFIRMED)

Phone + OTP, single endpoint does BOTH send and verify.

### Send OTP
```
POST /api/tenant/auth/sign-in
{ "phone": "+998933767534", "countryCode": "uz" }      // countryCode MUST be lowercase "uz"
→ 201 { "message": "OTP sent to your phone", "code": "45475", "telegramUrl": "https://t.me/send_otp_sms_bot" }
```
- `countryCode` = the country's `code` field from `/api/public/countries` (lowercase `"uz"`).
  Uppercase `"UZ"` passes the SEND but makes the VERIFY step fail with 404 "Country not found".
- **The `code` in the response IS the OTP** (echoed back — dev/staging convenience). Confirmed:
  verifying instantly with the echoed code does NOT return `OTP_INCORRECT` (it passes the OTP check).
- OTP delivery to the user is via a Telegram bot (`t.me/send_otp_sms_bot`) — likely SMS in prod.
- Country reference (`/api/public/countries`): single active country
  `{ id: "2a5fad7b-80de-49a6-b6a9-8da6b47822d7", code: "uz", name: "Uzbekistan", phoneCode: "+998", isActive: true }`.

### Verify OTP (existing user)
```
POST /api/tenant/auth/sign-in
{ "phone": "+998933767534", "countryCode": "uz", "code": "<OTP>" }
```
- `otpCode` is a separate BOOLEAN flag on this DTO (validation: "otpCode must be a boolean value") —
  NOT the code value. The OTP value goes in `code`.
- **BLOCKED HERE:** with correct `countryCode:"uz"` + the correct `code`, the OTP validation passes
  but the request then returns **500 Internal Server Error** on completion — consistently, for the
  test account (+998933767534). Adding `fcmToken`/`otpCode:true` did not help. This looks like a
  server-side bug/edge in the existing-user re-auth completion (e.g. a null field during token
  issuance), NOT a request-shape problem on our side. **Needs backend-team insight or a different
  account to get past.** Everything downstream (chats, questions, files) is gated on obtaining a
  valid `accessToken` here.

### Other auth endpoints (INFERRED / partially confirmed)
- `POST /api/tenant/auth/sign-up` — NEW user registration. Required fields (from 400 validation):
  `fullname` (string), `type` (enum `FARMER`|`AGROSHOP`), `region` (UUID), `district` (UUID),
  + presumably `phone`/`countryCode`/`code`.
- `POST /api/tenant/auth/get-access-token` — token REFRESH. Body: `{ "refreshToken": "<jwt, ≥6 chars>" }`.
- `GET /api/tenant/auth/get-me` — current user (needs bearer). Not yet exercised (blocked on token).

## Endpoints still to confirm (blocked on a valid access token)

All INFERRED from APK strings; exact request/response shapes NOT yet captured:
- `POST /api/tenant/chats/start` — start a chat. Body likely `{ crop, field, ... }` (app has crop AND
  field pickers: `showCropSelectionSheet`/`showFieldSelectionSheet`). Chat object:
  `{ id, title, state:{ crop:{id}, currentQuestion }, finished, draft, ... }`.
- `GET /api/tenant/question-options/by-question/{question_id}` — options for the current question.
- Answer submission — REST (`optionId`) vs Socket.IO emit UNRESOLVED. Socket.IO is in use
  (`/socket.io`, `get:socketUrl`, candidate event `start_chat`).
- `POST /api/tenant/files` — photo upload; app method `uploadFile`, returns an `imageUrl` (INFERRED).
- `GET/POST /api/tenant/messages`, `/messages/chat/{id}`, `/messages/read/` (batch `seen_message_ids`).
- Other tenant routes seen: `/api/tenant/crops`, `/diseases`, `/weeds`, `/treatments`, `/fields`,
  `/regions`, `/districts`, `/forecast/daily`, `/notifications`, `/feedback`, `/plantings`.

## Cross-check with growz_ai microservice

`growz_ai`'s `POST /v1/disease-detection` takes `{image_urls, chat_id, crop_id, problem_type(1/2/3),
plant_part(1/2/3/4), user_info:{full_name, region_id}}` and `chat_id` is documented as "UUID from the
main API" — strong signal the main Growz backend calls growz_ai as its AI oracle, keyed by the same
chat the `/api/tenant/chats/*` endpoints manage. Photos are passed as URLs (`image_urls`), matching the
`/tenant/files` → `imageUrl` upload pattern.

## Next step to unblock

Obtain a valid `accessToken` for a Growz account (bypassing the 500 on OTP completion) — either from a
logged-in session, a dev/staging account on `dev2-api.growz.io`, or by the backend team resolving the
500. With a token, the remaining 6 unknowns above can be confirmed in minutes against `/api/tenant/*`.

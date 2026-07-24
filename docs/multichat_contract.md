# MULTICHAT + HYBRID GUIDED FLOW — SHARED CONTRACT (v2)

This document is the ONLY coordination between the backend implementer and the
mobile implementer. Both build in parallel from this file. Field names, event
types, JSON shapes, step ids, phase ids, tool names and Uzbek strings below are
EXACT and FROZEN.

v2 EXTENDS the deployed v1 implementation (do not rewrite v1 code — extend
it). Everything not marked **NEW in v2** or **CHANGED in v2** is already
built, deployed and byte-frozen; keep it exactly as it is.

Repo: `/Users/rustamjonakhmedov/Herd/betterfuture/voiceagents/voiceagent-google`
Backend root: `backend/` (FastAPI, `app/…`). Mobile root: `mobile/` (Flutter,
`lib/…`).

---

## 0. Design summary + justification

### 0.1 What v2 adds (Phase 1 of the "rAIs Hybrid Chat — Final AI Logic Flow")

| # | Feature | Mechanism |
|---|---------|-----------|
| 1 | 3-option entry: `general` («Umumiy savol berish») joins `disease_pest` and `weed` at the `query_type` step | one new option in the existing step table |
| 2 | Symptom-dialogue phase (spec §2) between `plant_part` and `photo` for `disease_pest` chats — diagnosis is never photo-only | new derived step `symptom`, new phase `"symptom"`, new Live tool `to_photo`, new on-screen button «Rasmga oʻtish» |
| 3 | Full General Question Flow (spec §A) when `query_type == "general"` | new phase `"general"`, general policy `[TIZIM]` script + connect-time block, §0.3 trigger-word detection (model-side AND server-side), new Live tool `switch_to_diagnostic`, on-screen offer buttons (`diag_offer`) |
| 4 | Crop profile shortcut (spec §1.1, light) | the farmer's remembered crops (memory profile) become quick-pick chips prepended to the `crop` step's `chat.question.options` |

DEFERRED (NOT in v2 / Phase 1): Growz-profile auto-pull (spec §1.2),
multi-image ranking (§5), structured preparations / Marketplace product cards
(§6 — Agroapteka is a TEXT-level mention only), human-agronom loop (§7).

### 0.2 Why this coexists with the fixed Gemini Live connect-time prompt

Same constraint and same answer as v1: the Live connection fixes its system
prompt and tool list at connect time, so nothing phase-dependent may ride the
prompt. v2 keeps that discipline:

* The **tool list is a superset declared at connect**: `select_option` (v1) +
  `to_photo` + `switch_to_diagnostic` (v2) are ALL declared whenever a session
  binds an unfinished chat. Tools that are out-of-phase simply return an
  `invalid` ack with a corrective Uzbek note — declaring them early is
  harmless because the handler, not the declaration, enforces the phase.
* The **symptom phase** and the **general phase** are switched on by
  server-authored `[TIZIM]` text turns at the exact moment the phase begins
  (scripts §4.6 SY/G) — the same proven mechanism v1 uses for every step
  question and the consult kickoff. The connect-time policy block only says
  "when a `[TIZIM]` instruction arrives, follow it".
* The only phase-specific CONNECT-TIME block is for **resumed** chats, where
  the phase is already known before connect: a resumed unfinished `general`
  chat gets the general policy block (§4.5), exactly like v1 gives a resumed
  finished chat the history recap.

### 0.3 v1 design recap (unchanged)

The guided flow lives on the backend (`ChatGuide`), driven over the existing
WS. Rais speaks each question via `[TIZIM]` text-turn injection
(`_speak_text`/`speak_system`); the server simultaneously sends a
deterministic `chat.question` that renders buttons; a tap (`chat.answer`) or a
spoken answer (Live tools) funnel into the same accept path. Chats persist in
`data/chats/` (atomic files); three REST endpoints serve list/create/get.
Everything is FAIL-OPEN (§8).

---

## 1. WebSocket protocol (v2)

All events ride the existing JSON control plane of `/ws/voice`. Binary frames
stay mic-audio-only. Unknown event types/kinds/phases are ignored by both
sides — forward compatible.

### 1.1 `session.start` (client → server) — UNCHANGED from v1

`chat_id` binds the session to a stored chat; empty/absent/unknown/mismatched
→ plain session. No new fields in v2.

### 1.2 `chat.state` (server → client) — CHANGED in v2: two new phases

Emitted (a) right after the Live session is up for a chat-bound session,
(b) on every phase transition, (c) when the guide degrades on error.

```json
{
  "type": "chat.state",
  "chat_id": "9f2c…",
  "phase": "symptom",
  "selections": {
    "query_type": "disease_pest",
    "crop_id": "6a91…-growz-uuid",
    "crop_name": "Pomidor",
    "plant_part": "leaf",
    "photo_id": ""
  }
}
```

* `phase` ∈ `"guide"` | `"symptom"` **(NEW)** | `"general"` **(NEW)** |
  `"consult"`.
  * `guide` — a button step (`query_type`/`crop`/`plant_part`/`photo`) is
    pending.
  * `symptom` — the voice symptom dialogue is running (a persistent
    `chat.question` with `kind:"symptom"` is on screen, see §1.3).
  * `general` — the general-question conversation is running (a persistent
    `kind:"free"` hint question, plus possibly a `diag_offer` question).
  * `consult` — no pending guided step; free consultation. Hides the options
    bar (v1 behaviour, unchanged).
* `selections`: EXACTLY the v1 five keys, always present, `""` when unset. No
  new keys in v2.
* Mobile: only `"consult"` triggers question-clearing (v1 rule). `"symptom"`
  and `"general"` are stored in `guidePhaseProvider` as-is; the options bar
  keeps being driven purely by `chat.question`. An old (v1) app receiving the
  new phases stores the string and behaves correctly by construction.

Phase transition emissions (exact):
* entering the symptom step → `chat.state{phase:"symptom"}`
* symptom finished (to_photo/button/backstop) → `chat.state{phase:"guide"}`
  (the photo step is a guide step)
* `query_type = general` accepted → `chat.state{phase:"general"}`
* `switch_to_diagnostic` accepted → `chat.state{phase:"guide"}`
* guide finished / degraded → `chat.state{phase:"consult"}` (v1, unchanged)

### 1.3 `chat.question` (server → client) — CHANGED in v2: new steps + kinds

At most ONE question is pending at a time; every emission replaces the
previous one. `step_id` ∈ `query_type | crop | plant_part | symptom | photo |
general | diag_offer`. `kind` ∈ `buttons | crop_picker | photo | symptom |
free`.

**(a) `query_type` — CHANGED: third option**

```json
{
  "type": "chat.question",
  "chat_id": "9f2c…",
  "step_id": "query_type",
  "prompt": "Nima boʻyicha maslahat kerak?",
  "kind": "buttons",
  "options": [
    {"id": "disease_pest", "label": "Kasalliklar va zararkunandalar"},
    {"id": "weed", "label": "Begona oʻt"},
    {"id": "general", "label": "Umumiy savol berish"}
  ]
}
```

Note the REVISED prompt string (§6): `qQueryType` is now
`Nima boʻyicha maslahat kerak?` (was `Qanday muammo boʻyicha yordam kerak?`).
The wire carries the prompt, so mobile renders whatever arrives; the
`strings.dart` mirror is updated for consistency only.

**(b) `crop` — CHANGED: memory-crop quick-pick chips prepended**

```json
{
  "type": "chat.question",
  "chat_id": "9f2c…",
  "step_id": "crop",
  "prompt": "Qaysi ekin haqida gaplashamiz?",
  "kind": "crop_picker",
  "options": [
    {"id": "6a91…-growz-uuid", "label": "Pomidor"},
    {"id": "83bd…-growz-uuid", "label": "Bodring"},
    {"id": "open_crop_picker", "label": "Ekinlar"}
  ]
}
```

* Every option whose `id != "open_crop_picker"` is a **memory-crop chip**: a
  crop from the farmer's memory profile that the server has ALREADY resolved
  against the Growz catalogue — `id` is the real Growz crop UUID, `label` is
  the catalogue display name. 0–4 chips (§4.9); chips first, the
  `open_crop_picker` option ALWAYS last.
* Mobile rendering (`kind == "crop_picker"`, v2): render each non-
  `open_crop_picker` option as a tappable chip that sends
  `chat.answer{step_id:"crop", option_id:<id>, value:<label>}` — identical to
  a sheet pick; render the `open_crop_picker` option as the button that opens
  the crop sheet (v1 behaviour). Chips do NOT touch `selectedCropProvider`
  (the server stores the crop on the ChatDoc; resumed sessions get the crop
  from the server-side fallback).
* Old (v1) app: its `crop_picker` case ignores `options` entirely and shows
  only the «Ekinlar» button — chips silently absent, flow intact.

**(c) `symptom` — NEW step, NEW kind**

Emitted when the symptom phase starts (and re-emitted on resume). It PERSISTS
across the whole voice symptom dialogue (many farmer/Rais turns) until a
`chat.step{step_id:"symptom"}` clears it.

```json
{
  "type": "chat.question",
  "chat_id": "9f2c…",
  "step_id": "symptom",
  "prompt": "Belgilar haqida gapirib bering",
  "kind": "symptom",
  "options": [
    {"id": "to_photo", "label": "Rasmga oʻtish"}
  ]
}
```

* Mobile rendering (`kind == "symptom"`): IDENTICAL widget tree to `buttons`
  (caption + tonal chips). The separate kind exists because (1) this bar stays
  up across many turns instead of being answered once, and (2) future styling
  may differ. Tapping «Rasmga oʻtish» sends
  `chat.answer{step_id:"symptom", option_id:"to_photo", value:""}`.
* Old (v1) app: falls into the `default:` buttons branch → renders the one
  chip → tap sends the same `chat.answer`. Fully functional.

**(d) `photo` — UNCHANGED wire shape** (v1 §1.3). Reached after `symptom` for
`disease_pest`, directly after `crop` for `weed`.

**(e) `general` — NEW step, NEW kind `free`**

Emitted when the general phase starts (and re-emitted on resume and after a
declined `diag_offer`). A caption-only hint; NO input widgets.

```json
{
  "type": "chat.question",
  "chat_id": "9f2c…",
  "step_id": "general",
  "prompt": "Savolingizni bemalol ayting",
  "kind": "free",
  "options": []
}
```

* Mobile rendering (`kind == "free"`): show the `prompt` caption exactly like
  every other question, render NO buttons (`SizedBox.shrink()` input). The
  farmer talks (PTT) or types (existing text fallback). The §A2 clarifying
  questions are asked by Rais BY VOICE and answered by voice/text — they are
  adaptive and model-chosen, so there are deliberately no deterministic
  context chips for them.
* Old (v1) app: `default:` buttons branch with zero options → caption only.
  Same result.

**(f) `diag_offer` — NEW step, kind `buttons`** (spec §A4 / §0.3)

Emitted DURING the general phase when the server detects a disease/pest
trigger word in a committed farmer turn (§4.10). Replaces the `general` hint
question. At most ONE offer per session.

```json
{
  "type": "chat.question",
  "chat_id": "9f2c…",
  "step_id": "diag_offer",
  "prompt": "Aniqlash jarayonini boshlaymizmi?",
  "kind": "buttons",
  "options": [
    {"id": "switch_diag", "label": "Ha, aniqlaymiz"},
    {"id": "stay_general", "label": "Yoʻq, davom etamiz"}
  ]
}
```

Old (v1) app renders and answers it natively (plain buttons).

### 1.4 `chat.step` (server → client) — UNCHANGED shape, new step ids

Acknowledges an accepted answer regardless of path (tap, voice tool, server
backstop). The client clears the pending question and shows the `✓ <label>`
chip (v1 behaviour). New emissions in v2:

| trigger | payload |
|---|---|
| `query_type = general` accepted | `{"type":"chat.step","chat_id":…,"step_id":"query_type","option_id":"general","value":"","label":"Umumiy savol berish"}` |
| symptom finished (button tap, `to_photo` tool, or 12-turn backstop §4.8) | `{"type":"chat.step","chat_id":…,"step_id":"symptom","option_id":"to_photo","value":"<symptom_summary or empty>","label":"Rasmga oʻtish"}` |
| diagnostic switch accepted (tap `switch_diag` or `switch_to_diagnostic` tool) | `{"type":"chat.step","chat_id":…,"step_id":"diag_offer","option_id":"switch_diag","value":"","label":"Ha, aniqlaymiz"}` |
| offer declined (tap `stay_general`) | `{"type":"chat.step","chat_id":…,"step_id":"diag_offer","option_id":"stay_general","value":"","label":"Yoʻq, davom etamiz"}` |

Mobile needs NO code change: `ChatStepAck` handling (clear question + `✓`
chip) already covers these. `guideSelectionsProvider.put()` receiving the new
step ids is harmless.

### 1.5 `chat.answer` (client → server) — UNCHANGED shape, new accepted values

* `query_type` step: `option_id` ∈ {`disease_pest`, `weed`, `general`}
  **(CHANGED — third id)**, `value: ""`.
* `crop` step: `option_id` = Growz crop UUID (from the sheet OR a memory
  chip), `value` = crop display name. UNCHANGED server validation (any
  non-empty `option_id` from the tap path is accepted with `label = value`).
* `plant_part` step: unchanged (9-id enum).
* `symptom` step **(NEW)**: only `option_id: "to_photo"`, `value: ""` is
  accepted; anything else ignored silently.
* `photo` step: unchanged (`skip` only; a real photo advances via
  `photo.upload`).
* `diag_offer` step **(NEW)**: `option_id` ∈ {`switch_diag`, `stay_general`},
  `value: ""`. Accepted ONLY while the chat's pending step is `general`
  (§4.4); otherwise ignored silently. NOTE: `diag_offer` is the one step id
  that is VALID in `chat.answer` while `pending_step()` returns a DIFFERENT id
  (`general`) — the server special-cases it (§4.4 f).
* `general` step: has no options; any `chat.answer{step_id:"general"}` is
  ignored silently.

Stale/mismatched/unknown answers: ignored silently (v1 rule, unchanged).

### 1.6 Live tools (v2: three declarations)

Attached via `session.set_tool_extension(...)` ONLY when the session is bound
to a chat with `finished == false` (v1 wiring, unchanged — note a `general`
chat stays unfinished for life, §3.2, so its tools are wired on every resume).
`ChatGuide.build_tools()` now returns ONE `types.Tool` with THREE function
declarations:

```python
select_option = types.FunctionDeclaration(          # UNCHANGED from v1
    name="select_option",
    description=(
        "Yoʻriqli soʻrov bosqichida fermer OGʻZAKI javob berganda "
        "uning tanlovini qayd etish. step_id — hozirgi bosqich; "
        "option_id — variant identifikatori; ekin bosqichida "
        "option_id oʻrniga value maydonida fermer aytgan ekin "
        "nomini yubor."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "step_id": types.Schema(
                type=types.Type.STRING,
                enum=["query_type", "crop", "plant_part", "photo"],
            ),
            "option_id": types.Schema(type=types.Type.STRING),
            "value": types.Schema(type=types.Type.STRING),
        },
        required=["step_id"],
    ),
)

to_photo = types.FunctionDeclaration(               # NEW in v2
    name="to_photo",
    description=(
        "Belgilar suhbati yetarli boʻlganda — muammo belgilari, "
        "davomiyligi va tarqalishi aniq boʻlgach — rasm bosqichiga "
        "oʻtish. Faqat belgilar bosqichida chaqir. summary — "
        "belgilarning 1-2 jumlalik xulosasi (oʻzbekcha)."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "summary": types.Schema(type=types.Type.STRING),
        },
    ),
)

switch_to_diagnostic = types.FunctionDeclaration(   # NEW in v2
    name="switch_to_diagnostic",
    description=(
        "Umumiy savol suhbatida kasallik yoki zararkunanda belgilari "
        "sezilsa va fermer aniqlashga rozi boʻlsa, aniqlash (tashxis) "
        "jarayonini boshlash. Faqat umumiy savol rejimida chaqir."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "reason": types.Schema(type=types.Type.STRING),
        },
    ),
)

def build_tools(self):
    return [types.Tool(function_declarations=[
        select_option, to_photo, switch_to_diagnostic,
    ])]
```

**When Rais calls them** (taught by the `[TIZIM]` scripts §4.6, not by the
prompt):

* `select_option` — v1 behaviour, unchanged. `query_type` step now also
  accepts a spoken «umumiy savol» → `option_id: "general"` (the option is in
  the step table, so v1 validation accepts it with zero code change).
* `to_photo` — during the symptom phase, the moment Rais judges the context
  sufficient (spec §2.2 "Ha → photo"): after the main questions are covered
  or at most ~10 follow-ups. `summary` = a 1–2 sentence Uzbek recap of the
  symptoms (optional but requested by the script).
* `switch_to_diagnostic` — during the general phase, AFTER Rais has said the
  §A4 offer sentence and the farmer agreed (or the farmer himself asks to
  identify the problem). `reason` is free-text, stored nowhere in Phase 1.

**`handle_tool` routing** (extends the v1 handler; every branch fail-open,
returns an ack dict, NEVER raises):

```
name == "select_option"        → v1 logic, unchanged
name == "to_photo"             → accepted iff pending_step() == "symptom"
name == "switch_to_diagnostic" → accepted iff pending_step() == "general"
anything else                  → None  (falls through to unknown_tool ack)
```

Ack contract (all three tools): accepted →
`{"status": "recorded", "note": "<Uzbek next-instruction script>"}`; rejected
→ `{"status": "invalid", "note": "<Uzbek corrective script>"}` (§4.6 D
family). `select_option` called while pending is `symptom`/`general` gets the
phase-specific corrective note (§4.6 D-SY / D-G), not an exception.

---

## 2. REST endpoints — UNCHANGED from v1

`GET /chats`, `POST /chats`, `GET /chats/{chat_id}` exactly as v1 §2 (shapes,
validation, nginx carve-out — all deployed). The summary/detail payloads gain
three additive fields from §3.2 (`symptom_done`, `symptom_summary`,
`general_question`); mobile's tolerant `fromJson` ignores them — no mobile
change required.

---

## 3. Backend chat storage (v2 deltas)

Store mechanics (layout, atomic writes, `.bad` sidelining, caps, `ChatStore`
API) — UNCHANGED from v1 §3.

### 3.2 `ChatDoc` — CHANGED in v2: three new fields, widened `query_type`

```python
class ChatDoc(BaseModel):
    id: str
    user_id: str
    title: str = UZ["newChatTitle"]
    query_type: str = ""        # "" | disease_pest | weed | general   (CHANGED)
    crop_id: str = ""
    crop_name: str = ""
    plant_part: str = ""        # "" | one of TARGET_PARTS
    symptom_done: bool = False  # NEW — symptom dialogue completed (to_photo)
    symptom_summary: str = ""   # NEW — to_photo's summary arg, <=300 chars
    general_question: str = ""  # NEW — first farmer turn of the general
                                #       phase, <=300 chars
    created_at: str = ...
    updated_at: str = ...
    finished: bool = False
    last_diagnosis: dict | None = None
    messages: list[ChatMessage] = ...
```

* Old on-disk docs load fine (pydantic defaults). New fields are additive in
  `build_summary` AND `build_detail` (both include `symptom_done`,
  `symptom_summary`, `general_question`).
* `finished` semantics: `true` when the DIAGNOSTIC guided flow completed
  (photo step done/skipped) or the guide degraded. A `general` chat stays
  `finished == false` for its whole life (that is what keeps the
  `switch_to_diagnostic` tool wired on every resume); it only becomes
  `finished` if it degrades, or if it switches to diagnostic and completes
  that flow.

### 3.6 Title derivation — CHANGED in v2 (general rule first)

Recomputed whenever a selection lands AND when `general_question` is first
captured; first matching rule wins:

1. **NEW:** `query_type == "general"` → `general_question[:60]` if set; else
   the first `farmer` message with `kind == "text"`, truncated to 60; else
   `"Umumiy savol"` (`UZ["generalTitle"]`).
2. `crop_name` set → `"{crop_name} — kasallik"` for `disease_pest`,
   `"{crop_name} — begona oʻt"` for `weed`, bare `crop_name` otherwise. (v1)
3. `query_type` set → its full option label (now including
   `"Umumiy savol berish"`, though rule 1 fires first for general). (v1)
4. first farmer `text` message → its text[:60]. (v1)
5. `"Yangi suhbat"`. (v1)

A chat that switched general → diagnostic keeps `general_question` on the doc
but `query_type` is `disease_pest`, so rules 2–5 apply — the title becomes
`"Pomidor — kasallik"` etc. as usual.

---

## 4. The guided-flow state machine (v2)

### 4.1 Phases and steps — CHANGED in v2

```
DIAGNOSTIC (query_type = disease_pest):
  phase "guide":   query_type → crop → plant_part
  phase "symptom":                          symptom (voice dialogue, §2 spec)
  phase "guide":                                     photo
  phase "consult": free consultation → diagnosis

WEED (query_type = weed):
  phase "guide":   query_type → crop → photo     (plant_part AND symptom
  phase "consult": …                              are both SKIPPED; photo
                                                  targets whole_plant — v1)

GENERAL (query_type = general):
  phase "general": open conversation per spec §A (question → 0–5 context
                   questions → structured answer; Agroapteka text mention).
                   Never finishes on its own. A trigger (§4.10) or the
                   switch tool converts it to DIAGNOSTIC at the crop step.
```

`STEP_ORDER = ["query_type", "crop", "plant_part", "symptom", "photo"]`
(**CHANGED** — `symptom` inserted). `general` and `diag_offer` are step IDS
(they appear in `STEPS`, `chat.question.step_id` and `chat.step.step_id`) but
NOT in `STEP_ORDER` — `general` is a phase-carrying pseudo-step, `diag_offer`
is an overlay question inside the general phase.

**Pending-step derivation — CHANGED in v2 (exact):**

```python
def pending_step(self) -> str | None:
    d = self.doc
    if not d.query_type:
        return "query_type"
    if d.query_type == "general":
        return None if d.finished else "general"
    if not d.crop_id:
        return "crop"
    if d.query_type != "weed" and not d.plant_part:
        return "plant_part"
    if d.query_type == "disease_pest" and not d.symptom_done:
        return "symptom"
    if not d.finished:
        return "photo"
    return None
```

**Phase mapping (exact):**

```python
PHASE_FOR_STEP = {
    "query_type": "guide", "crop": "guide", "plant_part": "guide",
    "symptom": "symptom", "general": "general", "photo": "guide",
}
# pending None → "consult"
```

Resume works by derivation exactly as v1: an unfinished chat re-enters at its
pending step — including `symptom` and `general` (§4.11).

### 4.2 Step definitions — CHANGED in v2 (single source of truth: `models.py` `STEPS`)

| step_id      | kind          | options `[{id, label}]`                                                                 | answer accepted |
|--------------|---------------|------------------------------------------------------------------------------------------|-----------------|
| `query_type` | `buttons`     | `disease_pest` → S.optDiseasePest; `weed` → S.optWeed; **`general` → S.optGeneral (NEW)** | one of the THREE ids (tap or `select_option`) |
| `crop`       | `crop_picker` | 0–4 memory-crop chips `{Growz UUID, catalogue name}` (**NEW**, §4.9) + `open_crop_picker` → S.optCrops LAST | `option_id` = Growz UUID, `value` = name (chip, sheet, or `select_option` fuzzy match — v1) |
| `plant_part` | `buttons`     | the 9 enum ids (v1 §4.3) with S.part* labels                                              | one of the 9 ids |
| `symptom` **(NEW)** | `symptom` | `to_photo` → S.optToPhoto                                                          | `to_photo` via tap OR the `to_photo` Live tool OR the 12-turn backstop (§4.8) |
| `photo`      | `photo`       | `take_photo` → S.optTakePhoto; `skip` → S.optSkipPhoto                                    | `skip` via `chat.answer`; a real photo via `photo.upload` (v1) |
| `general` **(NEW)** | `free` | *(none)*                                                                            | never answered directly; exits via `diag_offer`/`switch_to_diagnostic` |
| `diag_offer` **(NEW)** | `buttons` | `switch_diag` → S.optSwitchDiag; `stay_general` → S.optStayGeneral               | one of the two ids via tap; the voice path is the `switch_to_diagnostic` tool (accept) or simply continuing to talk (decline) |

`prompt` strings: S.qQueryType (REVISED) / S.qCrop / S.qPlantPart /
S.qSymptom (NEW) / S.qPhoto / S.qGeneral (NEW) / S.qDiagOffer (NEW) — §6.

`STEPS` gains entries `"symptom"`, `"general"`, `"diag_offer"` (kinds and
options per this table) so `_label_for`/`_opts_str`/`_emit_question` keep
working unchanged. The `select_option` enum does NOT gain the new ids (§1.6).

### 4.3 `plant_part` options — UNCHANGED (v1 table).

### 4.4 Advancing on each answer — CHANGED in v2

The v1 accept path (persist → `chat.step` → speak/ack → next question or
finish) is unchanged for `query_type(disease_pest|weed)`, `crop`,
`plant_part`, `photo`. ONE UNIVERSAL RULE kept from v1: every accepted answer
(any path — tap, voice tool, backstop) appends
`{role:"farmer", kind:"answer", text:<label>}` and recomputes the title.

New/changed transitions (exact emission order in each):

**(a) `plant_part` accepted (disease_pest path) → symptom phase**
1. persist `plant_part`, append answer message, save; emit
   `chat.step{plant_part}` (v1);
2. `pending_step()` → `"symptom"` → emit `chat.state{phase:"symptom"}`;
3. emit `chat.question{symptom}` (persists the
   `{role:"rais", kind:"question", text:S.qSymptom}` message);
4. tap path: `speak_raw(SCRIPT SY-B)`; voice path: return SCRIPT SY-C as the
   `select_option` ack note (§4.6). Never both.

**(b) symptom finished — by `chat.answer{symptom,to_photo}` tap, `to_photo`
tool, or the backstop (§4.8)**
1. set `symptom_done = True`; set `symptom_summary = summary[:300]` when the
   tool provided one; append answer message (`text: "Rasmga oʻtish"`), save;
2. emit `chat.step{step_id:"symptom", option_id:"to_photo",
   value:<symptom_summary>, label:"Rasmga oʻtish"}`;
3. emit `chat.state{phase:"guide"}`;
4. emit `chat.question{photo}` (v1 shape);
5. tap → speak SCRIPT B with the photo step's SPOKEN prompt (§4.6 note);
   tool → SCRIPT C as ack note; backstop → speak SCRIPT SY-CAP.

**(c) `query_type = "general"` accepted → general phase**
1. persist `query_type = "general"`, append answer message, save; emit
   `chat.step{query_type, general}`;
2. emit `chat.state{phase:"general"}`;
3. emit `chat.question{general}` (kind `free`; persists the question message
   once);
4. tap → speak SCRIPT G-B; voice → SCRIPT G-C as ack note.

**(d) server trigger detection during general (§4.10) — at most once/session**
1. emit `chat.question{diag_offer}` (replaces the `general` hint; persists
   its question message);
2. `speak_raw(SCRIPT TRIG)`.
No state change; phase stays `general`.

**(e) diagnostic switch accepted — tap `switch_diag` OR the
`switch_to_diagnostic` tool** (tool valid even without a prior offer —
Rais may switch on his own semantic detection after farmer consent)
1. set `query_type = "disease_pest"` (BOTH §0.3 trigger groups — disease AND
   pest — map to the single Phase-1 combined bucket), recompute title, save;
   append answer message (`text: "Ha, aniqlaymiz"`);
2. emit `chat.step{diag_offer, switch_diag}`;
3. emit `chat.state{phase:"guide"}`;
4. emit `chat.question{crop}` (WITH memory chips, §4.9);
5. tap → speak SCRIPT SW-B; tool → SCRIPT SW-C as ack note.
`pending_step()` now derives `crop` → `plant_part` → `symptom` → `photo` as a
normal diagnostic chat. `general_question` stays on the doc as context.

**(f) offer declined — tap `stay_general`**
(`chat.answer{step_id:"diag_offer"}` is accepted while `pending_step()` is
`"general"` — the ONE special case where the answered step id differs from
the pending step; guide's `on_answer` special-cases it before the v1
`step_id != pending → ignore` check.)
1. append answer message (`text: "Yoʻq, davom etamiz"`); emit
   `chat.step{diag_offer, stay_general}`;
2. RE-emit `chat.question{general}` via the non-persisting resend path (v1
   `_resend_question` — no duplicate stored message);
3. `speak_raw(SCRIPT STAY)`. No further server offers this session.

**Photo step** advancing by real photo / skip / camera-cancel: UNCHANGED
(v1 §4.4), including `target_part` (= `plant_part`, or `whole_plant` for
weed). Spec §4 (a low-quality photo NEVER stops the flow) is already the
deployed behaviour of the existing photo pipeline — the guide only observes
it; nothing changes.

### 4.5 Connect-time prompt work — CHANGED in v2

In `voice_agent.py`, before `session.start()`, via `session.set_memory(...)`:

* **Unfinished chat, `query_type != "general"`** (new chat or resumed
  diagnostic): the v2 `_GUIDE_POLICY_BLOCK` (below) — v1 block plus the two
  final sentences. PLUS, when `doc.messages` is non-empty (resumed
  mid-guide/mid-symptom), append the SAME history recap builder used for
  finished chats (v1 §4.5 format: last 12 messages, 200 chars each, 2500-char
  cap) so a resumed symptom dialogue keeps its context.

```
[YOʻRIQLI SOʻROV] Suhbat boshida sen fermerdan bir nechta qisqa savol bilan
muammo turini, ekinni va oʻsimlik qismini aniqlaysan. [TIZIM][SAVOL] bilan
kelgan savolni OʻZ SOʻZLARING bilan qisqa ber (1-2 jumla), boshqa mavzuga
oʻtma. Ekranda fermerga tugmalar ham koʻrinadi — u bosishi yoki ogʻzaki
aytishi mumkin. MUHIM: har bosqichda fermerning OʻZI tugma bosishini yoki
ogʻzaki aytishini KUT. Eslagan maʼlumotlaring (masalan fermer avval qaysi
ekin ekkani yoki oldingi muammosi) asosida javobni OʻZING toʻldirma va
bosqichlarni oʻzing oʻtkazib yuborma — faqat fermer aytganini select_option
bilan yozib bor. Fermer OGʻZAKI javob bersa, DARHOL select_option
funksiyasini chaqir (step_id — hozirgi bosqich). Tugma bosilsa [TIZIM]
xabari keladi — qayta soʻrama, keyingi savolga oʻt. Yoʻriqli soʻrov
tugaguncha tashxis qoʻyma, rasm soʻrama va fermerning eski muammolarini
eslatma. Fermer «Umumiy savol berish»ni tanlasa yoki belgilar bosqichi
boshlansa, [TIZIM] xabaridagi koʻrsatmaga oʻt. Belgilar bosqichida savollar
berib, kontekst yetarli boʻlgach to_photo funksiyasini chaqirasan.
```

(The first ~10 sentences are byte-identical to the deployed v1 block; only
the LAST TWO sentences are appended in v2.)

* **Unfinished chat, `query_type == "general"`** (resumed general chat —
  NEW): the `_GENERAL_POLICY_BLOCK`:

```
[UMUMIY SAVOL] Bu suhbat umumiy savol rejimida. Fermer savolini aytadi.
Kontekst yetarli boʻlsa darhol javob ber; yetmasa BITTADAN, hammasi boʻlib
koʻpi bilan 5 ta aniqlashtiruvchi savol ber (qaysi ekin; qaysi
viloyat/tuman; qaysi faza; ekish sanasi; oxirgi sugʻorish; oxirgi
oʻgʻitlash; tuproq/dala holati). Javob tartibi: qisqa xulosa; amaliy
tavsiya; nima qilish kerak; nimani qilmaslik; muddat/meʼyor (agar kerak
boʻlsa); keyingi qadam. Savol oʻgʻit, preparat yoki himoya vositasi haqida
boʻlsa: mos kategoriyani, taʼsir etuvchi modda yoki mahsulot turini va
ehtiyot chorasini ayt, Agroapteka boʻlimini matnda eslatib oʻt. Trigger
soʻzlar (kasallik: kasallik, kasal, dogʻ, sargʻayish, qorayish, qoʻngʻir
dogʻ, chirish, soʻlish, barg qurishi, barg buralishi, poya chirishi, ildiz
chirishi, zamburugʻ, bakteriya, virus, «nima boʻlgan», «nega bunday»,
«davolash kerak»; zararkunanda: zararkunanda, hasharot, qurt, shira, trips,
kana, kuya, bit, lichinka, barg yeyilgan, teshiklar bor, hasharot
koʻrinyapti, «qanday dori sepaman», «nima bilan ishlov beraman») — shular
yoki shunga oʻxshash belgilar sezilsa fermerga ayt: «Bu kasallik yoki
zararkunanda bilan bogʻliq boʻlishi mumkin. Aniqlash uchun bir nechta savol
beraman» va fermer rozi boʻlsa switch_to_diagnostic funksiyasini chaqir.
```

  PLUS the history recap when `doc.messages` is non-empty.

* **Finished chat**: the v1 history recap block, unchanged.
* Memory kickoff suppression, enrichment crop fallback, memory-block
  suppression while an unfinished guide owns the opening: ALL UNCHANGED from
  v1. (A resumed unfinished GENERAL chat also suppresses the memory block —
  same `not doc.finished` condition already deployed.)

### 4.6 Spoken scripts — v1 set unchanged; NEW scripts in v2 (EXACT templates)

`{q}` = the step's prompt; `{opts}` = the step's `id=«label»` list; `{label}`
= accepted answer's label; `{title}` = chat title. v1 scripts A, B, C, D
(+crop suffix), E, F stay byte-identical with TWO amendments:

* **Photo-step spoken prompt (CHANGED):** wherever a script speaks the
  `photo` step (`{q}` in A/B/C/D for `step=photo`), `{q}` is now the spec-§3
  sentence `_PHOTO_SPOKEN_Q` below — NOT `UZ["qPhoto"]` (which remains the
  on-screen caption):

```
_PHOTO_SPOKEN_Q = (
    "Aniqroq tahlil uchun rasm yuboring: umumiy koʻrinish, zararlangan joy "
    "yaqindan, imkon boʻlsa ildiz. Yuborganingizdan soʻng javob beraman."
)
```

* **Consult kickoff E (CHANGED):** gains an optional symptom clause. Exact v2
  template (clauses in `{}` appear only when their datum exists):

```
[TIZIM] Yoʻriqli soʻrov tugadi. Maʼlumotlar: ekin — {crop_name}; muammo
turi — {query_type label}{; qismi — {plant_part label}}{; belgilar —
{symptom_summary yoki "suhbatda aytilgan"}}{; rasm keldi | ; rasm yoʻq}.
Endi erkin suhbat: {agar belgilar yigʻilgan boʻlsa: "yigʻilgan belgilar va
rasm asosida tashxis jarayonini davom ettir; ishonch past boʻlsa yana 1-2
aniqlashtiruvchi savol ber." aks holda: "muammo belgilarini batafsil
soʻrab, tashxis jarayonini boshla."} Javobingni bitta qisqa savoldan
boshla.
```

  Concretely: when `symptom_done` is true the `belgilar` clause is present
  (`symptom_summary` if non-empty, else the literal `suhbatda aytilgan`) and
  the second sentence is the `davom ettir` variant; otherwise (weed path) the
  template collapses to the v1 text exactly.

**NEW scripts** (backend-only constants in `guide.py`; `[TIZIM]`-prefixed
ones go through `speak_raw`, un-prefixed ones are tool-ack `note` texts):

Shared symptom body `_SY_BODY`:

```
Endi belgilar suhbati — rasmdan OLDIN belgilarni ogʻzaki aniqlaysan; tashxis
hech qachon faqat rasmga qurilmaydi. Har safar FAQAT BITTA qisqa savol ber.
Asosiy savollar: «Nima sodir boʻlyapti?», «Belgilar qachondan beri bor?»,
«Muammo butun daladami yoki ayrim oʻsimliklardami?», «Belgilar
kuchayyaptimi yoki bir xilmi?», «Oxirgi ishlov qachon va nima bilan?»,
«Oxirgi sugʻorish qachon?», «Oxirgi oʻgʻitlash qachon?». Kerak boʻlsa
qoʻshimcha savollar (koʻpi bilan 10 ta): oʻsimlik belgilari, ishlov tarixi,
oziqlanish, sugʻorish, tuproq, oldingi ekin. Kontekst yetarli boʻlgach
DARHOL to_photo funksiyasini chaqir (summary — belgilarning 1-2 jumlalik
xulosasi). Ekranda fermerga «Rasmga oʻtish» tugmasi ham koʻrinadi — bosilsa
[TIZIM] xabari keladi.
```

Shared general body `_GEN_BODY`:

```
Fermerdan savolini soʻra: «Savolingizni bemalol ayting». Savolni eshitgach
kontekst yetarliligini baholab koʻr: yetarli boʻlsa darhol javob ber;
yetmasa BITTADAN, hammasi boʻlib koʻpi bilan 5 ta aniqlashtiruvchi savol
ber (qaysi ekin; qaysi viloyat/tuman; qaysi faza; ekish sanasi; oxirgi
sugʻorish; oxirgi oʻgʻitlash; tuproq/dala holati). Javob tartibi: qisqa
xulosa; amaliy tavsiya; nima qilish kerak; nimani qilmaslik; muddat/meʼyor
(agar kerak boʻlsa); keyingi qadam. Savol oʻgʻit, preparat yoki himoya
vositasi haqida boʻlsa: mos kategoriyani, taʼsir etuvchi modda yoki
mahsulot turini va ehtiyot chorasini ayt, Agroapteka boʻlimini matnda
eslatib oʻt. Suhbatda kasallik yoki zararkunanda belgilari sezilsa: «Bu
kasallik yoki zararkunanda bilan bogʻliq boʻlishi mumkin. Aniqlash uchun
bir nechta savol beraman» deb ayt va fermer rozi boʻlsa
switch_to_diagnostic funksiyasini chaqir.
```

* **SY-B — symptom entry after a TAPPED plant_part** (`speak_raw`):
  `[TIZIM] Fermer tugma orqali «{label}» deb tanladi. Buni bir soʻz bilan tasdiqla. [SAVOL step=symptom] {_SY_BODY}`
* **SY-C — symptom entry after a SPOKEN plant_part** (`select_option` ack
  note):
  `Qabul qilindi: «{label}». [SAVOL step=symptom] {_SY_BODY}`
* **SY-A — resume at the symptom step** (`speak_raw`, from `start()`):
  `[TIZIM][SAVOL step=symptom] Qisqa salomlash (1 jumla), soʻng davom et: {_SY_BODY}`
* **SY-CAP — 12-turn backstop fired** (`speak_raw`):
  `[TIZIM] Belgilar suhbati yetarli. [SAVOL step=photo] «{_PHOTO_SPOKEN_Q}» Variantlar: {photo opts}.`
  (photo `{opts}` = the v1 `_PHOTO_OPTS` string, unchanged.)
* **G-B — general entry after a TAPPED query_type** (`speak_raw`):
  `[TIZIM] Fermer tugma orqali «{label}» deb tanladi. [UMUMIY SAVOL] {_GEN_BODY}`
* **G-C — general entry after a SPOKEN query_type** (ack note):
  `Qabul qilindi: «{label}». [UMUMIY SAVOL] {_GEN_BODY}`
* **G-A — resume of an unfinished general chat** (`speak_raw`, from
  `start()`; the `_GENERAL_POLICY_BLOCK` is already in the prompt):
  `[TIZIM] Fermer avvalgi umumiy savol suhbatiga qaytdi ({title}). Qisqa salomlash (1 jumla), soʻng savol boʻyicha qanday yordam kerakligini soʻra. [UMUMIY SAVOL] qoidalari amalda qoladi.`
* **TRIG — server-detected trigger offer** (`speak_raw`):
  `[TIZIM] Fermer soʻzlarida kasallik yoki zararkunanda belgilariga ishora bor. Fermerga ayt: «Bu kasallik yoki zararkunanda bilan bogʻliq boʻlishi mumkin. Aniqlash uchun bir nechta savol beraman.» Fermer rozi boʻlsa switch_to_diagnostic funksiyasini chaqir. Ekranda tasdiqlash tugmalari ham chiqdi.`
* **SW-B — switch accepted by TAP** (`speak_raw`):
  `[TIZIM] Fermer aniqlash jarayoniga oʻtishni tanladi. Muammo turi: kasallik/zararkunanda. [SAVOL step=crop] «{qCrop}» Variantlar: {crop opts}.`
* **SW-C — switch accepted by TOOL** (`switch_to_diagnostic` ack note):
  `Aniqlash jarayoni boshlandi. Muammo turi: kasallik/zararkunanda. [SAVOL step=crop] «{qCrop}» Variantlar: {crop opts}.`
* **STAY — offer declined by tap** (`speak_raw`):
  `[TIZIM] Fermer umumiy savolda davom etishni tanladi. Savoliga qaytib javob berishni davom ettir.`
* **D-SY — `select_option` called during the symptom phase** (invalid ack
  note):
  `Hozir belgilar bosqichi: savollarni davom ettir, kontekst yetarli boʻlgach to_photo chaqir.`
* **D-G — `select_option` called during the general phase** (invalid ack
  note):
  `Hozir umumiy savol rejimi: savolga javob ber. Aniqlash kerak boʻlsa switch_to_diagnostic chaqir.`
* **D-TP — `to_photo` called outside the symptom phase** (invalid ack note):
  `Hozir belgilar bosqichi emas. Joriy bosqichni davom ettir.`
* **D-SW — `switch_to_diagnostic` called outside the general phase** (invalid
  ack note):
  `Hozir umumiy savol rejimi emas. Joriy bosqichni davom ettir.`

Crop `{opts}` with memory chips (**CHANGED** `_opts_str("crop")`): when chips
exist —
`«{chip label 1}», «{chip label 2}» (fermerning ekinlari, ekranda tez tanlash), open_crop_picker=«Ekinlar»`;
without chips: the v1 string `open_crop_picker=«Ekinlar»`.

### 4.7 Message recording rules — CHANGED in v2

* `guide` phase (button steps): ONLY structured records — v1 rule unchanged
  (questions/answers/photo marker; free speech NOT persisted).
* **`symptom` phase (NEW): every committed turn IS persisted** (`kind:"text"`,
  both roles) — the symptom dialogue is the diagnostic substance.
* **`general` phase (NEW): every committed turn IS persisted** — it is the
  consultation.
* `consult` phase: every committed turn (v1, unchanged).
* Implementation: `ChatGuide` gains
  `records_transcript() -> bool` = `finished or pending_step() in ("symptom", "general")`,
  and the `voice_agent._chat_turn_recorder` gate changes from
  `guide.finished` to `guide.records_transcript()`. The recorder ALSO calls
  `guide.note_farmer_turn(text)` (sync, §4.10) for every recorded farmer
  turn, BEFORE appending. Typed `text.input` turns flow through the same
  committed-turn hook (best-effort).
* Scribe corrections, diagnosis message, teardown finalize: UNCHANGED (v1
  §4.7).

### 4.8 Symptom phase: "enough context" decision

Three ways forward, first wins (all land in §4.4 b):

1. **Rais-driven (primary):** the model calls `to_photo` when the §2.2 check
   ("kontekst yetarlimi?") passes — the SY scripts instruct it, including the
   ~10-follow-up ceiling.
2. **Farmer-driven:** the «Rasmga oʻtish» button (`chat.answer{symptom,
   to_photo}`) — the farmer can always push forward.
3. **Server backstop (deterministic):** after `_SYMPTOM_TURN_CAP = 12`
   recorded FARMER turns within the symptom phase (counted by
   `note_farmer_turn`), the guide schedules the same advance itself
   (`asyncio.create_task`, fail-open) with empty summary and speaks SY-CAP —
   a model that never calls the tool cannot trap the farmer.

### 4.9 Memory-crop quick-pick chips (spec §1.1, light)

* `voice_agent.py` passes the loaded memory profile's crops to the guide:
  `chat_guide.set_memory_crops(list(mem_profile.crops))` — called inside the
  existing memory try-block, right after `load_for_device`, only when
  `chat_guide is not None` and `mem_profile is not None` (wrapped
  try/except; any failure → no chips).
* At the FIRST emission of the `crop` question in a session, the guide
  resolves chips (cached for the session): for each profile crop name in
  stored order, run the SAME normalization + match used by `_match_crop`
  (exact normalized name, else unique containment) against
  `app.voice.enrich.get_crops`; keep at most **4** resolved chips, deduped by
  Growz id; UNRESOLVED profile crops are dropped (never invent an id). Chip
  option: `{"id": <growz uuid>, "label": <catalogue display name>}`.
* Emission order in `chat.question.options`: chips first, `open_crop_picker`
  last (§1.3 b). The spoken `{opts}` string changes per §4.6.
* A tapped chip is processed by the UNCHANGED v1 crop tap path (non-empty
  `option_id`, `label = value`). Growz catalogue unavailable / zero matches →
  plain v1 options; never an error.
* Growz-profile auto-pull (planting date, phase, agrotechnics — spec §1.2)
  is DEFERRED; memory chips are the whole Phase-1 shortcut.

### 4.10 Trigger detection (spec §0.3) — server-side backstop

Model-side detection is primary (the general policy carries the word list and
the offer sentence; Rais calls `switch_to_diagnostic` after consent). The
server ADDITIONALLY runs a deterministic scan so a missed cue still surfaces
buttons:

```python
# models.py — FROZEN list (spec §0.3; both groups map to "disease_pest")
TRIGGER_WORDS: tuple[str, ...] = (
    # disease
    "kasallik", "kasal", "dogʻ", "sargʻayish", "qorayish", "qoʻngʻir dogʻ",
    "chirish", "soʻlish", "barg qurishi", "barg buralishi", "poya chirishi",
    "ildiz chirishi", "zamburugʻ", "bakteriya", "virus", "nima boʻlgan",
    "nega bunday", "davolash kerak",
    # pest
    "zararkunanda", "hasharot", "qurt", "shira", "trips", "kana", "kuya",
    "bit", "lichinka", "barg yeyilgan", "teshiklar bor",
    "hasharot koʻrinyapti", "qanday dori sepaman", "nima bilan ishlov beraman",
)
```

Matcher (in `guide.py`, mirrors `_norm_crop`/`_OKINA_RE`; normalization =
lowercase + fold `ʻ`/`’`/`'` → `'` + trim):

```python
_WORD_SPLIT_RE = re.compile(r"[^a-z0-9']+")

def has_trigger(text: str) -> bool:
    t = _norm(text)                       # _OKINA_RE.sub("'", text.lower())
    words = {w for w in _WORD_SPLIT_RE.split(t) if w}
    for trig in TRIGGER_WORDS:
        tn = _norm(trig)
        if " " in tn:                     # multi-word phrase → substring
            if tn in t:
                return True
        elif len(tn) >= 5:                # long word → word-prefix match
            if any(w.startswith(tn) for w in words):
                return True
        elif tn in words:                 # short word → exact word only
            return True                   #  (so "bit" never fires on "bitta")
    return False
```

`ChatGuide.note_farmer_turn(text)` (SYNC — called from the recorder hook,
§4.7; every branch fail-open):

1. `pending_step() == "general"`:
   * if `doc.general_question == ""`: set it to `text[:300]`, recompute
     title, save;
   * if no offer was made this session AND `has_trigger(text)`: mark offered,
     `asyncio.create_task(self._offer_diagnostic())` → §4.4 d.
2. `pending_step() == "symptom"`: increment the session's farmer-turn
   counter; at `_SYMPTOM_TURN_CAP` schedule the backstop advance (§4.8).
3. Anything else: no-op.

Scanning applies ONLY to general-phase farmer turns (Phase 1). At most one
server offer per session; a declined offer (stay_general) also suppresses
further ones. `switch_to_diagnostic` remains valid regardless.

### 4.11 `start()` behaviour — CHANGED in v2 (resume matrix)

| chat state on connect | emissions (in order) | spoken |
|---|---|---|
| finished | `chat.state{consult}` | SCRIPT F (v1) |
| pending ∈ {query_type, crop, plant_part, photo} | `chat.state{guide}`, `chat.question{step}` | SCRIPT A (v1; photo `{q}` = `_PHOTO_SPOKEN_Q`) |
| pending == symptom **(NEW)** | `chat.state{symptom}`, `chat.question{symptom}` | SCRIPT SY-A |
| pending == general **(NEW)** | `chat.state{general}`, `chat.question{general}` | SCRIPT G-A |

### 4.12 Ordering guarantees (happy paths)

**Diagnostic (disease_pest) — v2:**

```
client                            server
──────                            ──────
session.start{chat_id} ────────▶  connect Live (prompt: base+memory+enrich+guide-v2)
                       ◀────────  chat.state{phase:"guide"}
                       ◀────────  chat.question{query_type: 3 options}
                       ◀────────  (audio) Rais greets + asks Q1        [A]
tap disease_pest ──────────────▶
                       ◀────────  chat.step{query_type}
                       ◀────────  chat.question{crop + memory chips}
                       ◀────────  (audio) Rais asks crop               [B]
tap chip "Pomidor" ────────────▶
                       ◀────────  chat.step{crop}
                       ◀────────  chat.question{plant_part}            [B]
tap leaf ──────────────────────▶
                       ◀────────  chat.step{plant_part}
                       ◀────────  chat.state{phase:"symptom"}
                       ◀────────  chat.question{symptom: Rasmga oʻtish}
                       ◀────────  (audio) Rais starts symptom Qs       [SY-B]
(voice turns: Rais asks, farmer answers … all persisted)
      Rais calls to_photo  ──or── tap «Rasmga oʻtish» ──or── 12-turn cap
                       ◀────────  chat.step{symptom, to_photo}
                       ◀────────  chat.state{phase:"guide"}
                       ◀────────  chat.question{photo}
                       ◀────────  (audio) Rais asks for the photo      [C/B/SY-CAP]
photo.upload ──────────────────▶  photo.received + guide advance (v1)
                       ◀────────  chat.step{photo}
                       ◀────────  chat.state{phase:"consult"}
                       ◀────────  (audio) consult kickoff              [E v2]
```

**General:**

```
tap "Umumiy savol berish" ─────▶
                       ◀────────  chat.step{query_type, general}
                       ◀────────  chat.state{phase:"general"}
                       ◀────────  chat.question{general, kind:"free"}
                       ◀────────  (audio) Rais asks for the question   [G-B]
(farmer asks; Rais clarifies 0–5×; answers per §A3; turns persisted)
farmer says "barglarida dogʻ bor…"
                       ◀────────  chat.question{diag_offer}            (server trigger)
                       ◀────────  (audio) Rais offers diagnostics      [TRIG]
tap "Ha, aniqlaymiz" ──or── Rais calls switch_to_diagnostic
                       ◀────────  chat.step{diag_offer, switch_diag}
                       ◀────────  chat.state{phase:"guide"}
                       ◀────────  chat.question{crop + memory chips}   [SW-B/SW-C]
(… continues as the diagnostic flow above, from crop)
```

---

## 5. Mobile plan (v2 deltas — everything else stays as built)

Avatar band, transcript + `stt.corrected` patching, PTT pill, text-input
fallback, camera/confirm flow, diagnosis card, chat list, chat service,
providers wiring: UNTOUCHED.

### 5.4 `guide_options_bar.dart` — the ONLY widget change

`_buildInput` switch gains/changes cases:

```dart
case 'symptom':          // NEW — same rendering as buttons
  return Wrap(spacing: 8, runSpacing: 8, children: [
    for (final o in question.options)
      FilledButton.tonal(
        onPressed: disabled ? null : () => _answer(question, optionId: o.id),
        child: Text(o.label),
      ),
  ]);

case 'free':             // NEW — caption only, no input surface
  return const SizedBox.shrink();

case 'crop_picker':      // CHANGED — memory chips before the sheet button
  return Wrap(spacing: 8, runSpacing: 8, children: [
    for (final o in question.options)
      if (o.id != 'open_crop_picker')
        FilledButton.tonal(
          onPressed: disabled
              ? null
              : () => _answer(question, optionId: o.id, value: o.label),
          child: Text(o.label),
        ),
    FilledButton.tonalIcon(
      onPressed: disabled ? null : () => _openCropPicker(question),
      icon: const Icon(Icons.eco_outlined),
      label: const Text(S.optCrops),
    ),
  ]);
```

Notes:
* Memory chips send `chat.answer` exactly like a sheet pick and do NOT touch
  `selectedCropProvider`.
* The disable-until-ack logic (`_sentForStepId`) works unchanged for the new
  steps: tapping «Rasmga oʻtish» or a `diag_offer` button disables the bar
  until the matching `chat.step` arrives; the symptom bar otherwise stays
  ENABLED across voice turns (no answer sent → never disabled).
* `diag_offer` and the 3-option `query_type` need NO code — they are plain
  `buttons`.
* `_takePhoto` (photo step) is unchanged: `target_part` = the `plant_part`
  selection from `guideSelectionsProvider`, `whole_plant` when absent.

### 5.5 Providers / controller / events — NO structural changes

* `chat_providers.dart`: unchanged (`guidePhaseProvider` already stores any
  string; `guideSelectionsProvider.put` tolerates new step ids).
* `voice_session_controller.dart`: unchanged — `ChatStateEvent` handling
  clears the question ONLY on `'consult'` (correct for v2), `ChatQuestion`
  replaces the pending question, `ChatStepAck` clears + chips.
* `events.dart`: code unchanged; UPDATE the doc comments to list the v2
  vocabularies — `ChatStateEvent.phase`: `guide | symptom | general |
  consult`; `ChatQuestion.stepId`: `query_type | crop | plant_part | symptom
  | photo | general | diag_offer`; `ChatQuestion.kind`: `buttons |
  crop_picker | photo | symptom | free`.
* `strings.dart`: add/revise the §6 keys.
* `test/events_test.dart`: extend round-trips with the new kinds/step ids
  (values are opaque strings — expect pass-through).

---

## 6. Uzbek string table (Latin, `ʻ` U+02BB okina; `ʼ` U+02BC in taʼsir/meʼyor/maʼlumot)

Backend: `chat/models.py` `UZ = {...}`. Mobile: `lib/features/chat/strings.dart`
(`abstract final class S`). Keys = constant names on both sides. All v1 rows
not listed here are UNCHANGED and stay byte-frozen.

**CHANGED in v2 (one key):**

| key | v2 Uzbek string | was (v1) |
|-----|-----------------|----------|
| `qQueryType` | `Nima boʻyicha maslahat kerak?` | `Qanday muammo boʻyicha yordam kerak?` |

Both sides ship the new value in the same release. The prompt travels on the
wire, so a version skew only shows the other side's caption text — harmless.

**NEW in v2:**

| key | Uzbek string | used for |
|-----|--------------|----------|
| `optGeneral` | `Umumiy savol berish` | query_type third option label |
| `qSymptom` | `Belgilar haqida gapirib bering` | symptom question caption |
| `optToPhoto` | `Rasmga oʻtish` | symptom → photo button + `chat.step` label |
| `qGeneral` | `Savolingizni bemalol ayting` | general-phase hint caption |
| `qDiagOffer` | `Aniqlash jarayonini boshlaymizmi?` | trigger-offer caption |
| `optSwitchDiag` | `Ha, aniqlaymiz` | offer accept button + `chat.step` label |
| `optStayGeneral` | `Yoʻq, davom etamiz` | offer decline button + `chat.step` label |
| `generalTitle` | `Umumiy savol` | general-chat title fallback (§3.6 rule 1) |

Byte-equality matters (`chat.step.label` and stored message texts must match
these strings exactly on both sides) — do NOT substitute a plain `'` for `ʻ`.
Spoken `[TIZIM]` scripts (§4.6), `_PHOTO_SPOKEN_Q`, the policy blocks and
`TRIGGER_WORDS` are backend-only strings and do NOT appear in `strings.dart`.

---

## 7. File-by-file change list (v2)

### BACKEND FILES

| file | change |
|------|--------|
| `backend/app/voice/chat/models.py` | UZ: revise `qQueryType`; add the 8 new keys (§6). `STEP_ORDER`: insert `"symptom"` (§4.1). `STEPS`: add `general` option to `query_type`; add `symptom`, `general`, `diag_offer` entries (§4.2). ADD `TRIGGER_WORDS` (§4.10). `ChatDoc`: `symptom_done`, `symptom_summary`, `general_question`; widen `query_type` comment (§3.2). `derive_title`: general rule first (§3.6). `build_summary`/`build_detail`: include the 3 new fields. |
| `backend/app/voice/chat/guide.py` | NEW scripts + `_PHOTO_SPOKEN_Q` + `_SY_BODY`/`_GEN_BODY` + `_GENERAL_POLICY_BLOCK` + policy-block extension (§4.5, §4.6). `pending_step()` v2 + `PHASE_FOR_STEP` (§4.1). `build_tools()` → 3 declarations; `handle_tool` routes `to_photo`/`switch_to_diagnostic` (§1.6). `on_answer` handles `symptom`/`diag_offer` (incl. the diag_offer-while-pending-general special case, §4.4 f). `_accept` branches for the new transitions (§4.4). `note_farmer_turn` + `has_trigger` + `_offer_diagnostic` + `_SYMPTOM_TURN_CAP` backstop (§4.8, §4.10). `set_memory_crops` + chip resolution + crop-question options + `_opts_str("crop")` chips variant (§4.9). `records_transcript()` (§4.7). `start()` resume matrix (§4.11). Consult kickoff v2 (§4.6 E). `build_guide_prompt_block`: general block + unfinished-history recap (§4.5). |
| `backend/app/voice/pipeline/voice_agent.py` | `_chat_turn_recorder`: gate on `guide.records_transcript()`; call `guide.note_farmer_turn(text)` for recorded farmer turns (§4.7). In the memory try-block: `chat_guide.set_memory_crops(...)` when both exist (§4.9). NOTHING else changes (tool wiring, dispatch, teardown all as deployed). |
| `backend/app/schemas.py` | Comment-only: document the new `kind`/`phase`/`step_id` vocabularies on the v1 doc-models. |
| `backend/app/voice/providers/gemini_live.py` | NO changes (the v1 `set_tool_extension` / `on_turn_committed` seams carry v2 as-is). |
| `backend/app/config.py`, `backend/app/main.py`, `backend/app/voice/chat/store.py` | NO changes. |
| `backend/app/voice/tests/test_chat_models.py` | EXTEND — general title rule, new STEPS/STEP_ORDER shape, new UZ keys, summary/detail new fields, `TRIGGER_WORDS` frozen list. |
| `backend/app/voice/tests/test_chat_guide.py` | EXTEND — pending-step v2 (incl. weed skips symptom; general pending forever); symptom flow (tap/tool/backstop; ordering of step/state/question); general flow (question capture, title, trigger offer once, stay path); switch (tool + tap; re-entry at crop); out-of-phase tool acks (D-SY/D-G/D-TP/D-SW); memory-chip resolution (match, cap 4, dedupe, fail-open); `records_transcript` gating; `has_trigger` word-boundary cases ("bitta" must NOT fire "bit"; "kasalligi" MUST fire "kasal"). |

### MOBILE FILES (all under `mobile/`)

| file | change |
|------|--------|
| `lib/features/chat/strings.dart` | Revise `qQueryType`; add `optGeneral`, `qSymptom`, `optToPhoto`, `qGeneral`, `qDiagOffer`, `optSwitchDiag`, `optStayGeneral`, `generalTitle` (§6, exact bytes). |
| `lib/features/chat/guide_options_bar.dart` | ADD `case 'symptom'` (buttons rendering) and `case 'free'` (`SizedBox.shrink()` input); CHANGE `case 'crop_picker'` to render non-`open_crop_picker` options as answer chips before the «Ekinlar» button (§5.4). |
| `lib/core/protocol/events.dart` | Doc comments only: new `phase`/`kind`/`step_id` value lists (§5.5). No structural change. |
| `test/events_test.dart` | EXTEND — new kinds/step ids round-trip. |
| `lib/features/chat/chat_providers.dart`, `lib/features/session/voice_session_controller.dart`, `lib/features/chat/chat_list_screen.dart`, `lib/features/chat/chat_service.dart`, `lib/features/chat/chat.dart`, interview/transcript/camera/avatar files | NO changes. |

Infra: NO changes (nginx carve-out and volumes already deployed).

---

## 8. FAIL-OPEN rules (v2 additions on top of the v1 law — all v1 rules stand)

* **Out-of-phase tools**: `to_photo` outside `symptom`, `switch_to_diagnostic`
  outside `general`, `select_option` during `symptom`/`general` → `invalid`
  ack with the corrective note (§4.6 D-family). NEVER an exception; any
  unexpected exception inside a tool handler → log, `_degrade()` (v1),
  `{"status":"invalid","note":"Xatolik yuz berdi, davom eting."}`.
* **Trigger scan / note_farmer_turn**: wrapped try/except; any failure means
  no offer / no counter bump — the call continues untouched. The scheduled
  `_offer_diagnostic` / backstop tasks are themselves fail-open (exception →
  log + `_degrade()` at worst).
* **Symptom phase cannot trap**: the on-screen «Rasmga oʻtish» button always
  works, and the 12-farmer-turn backstop advances even if the model never
  calls `to_photo` and the farmer never taps.
* **Memory-chip resolution failure** (profile missing, `get_crops` down, zero
  matches): the crop question falls back to the exact v1 options — never an
  error, never a fabricated crop id.
* **General chats degrade like any other**: `_degrade()` sets
  `finished = true` → the next open is a plain consult resume with the
  history recap. A degraded mid-symptom chat likewise resumes as consult.
* **Stale answers**: `chat.answer` for `symptom`/`diag_offer`/`general` with
  a mismatched pending step → ignored silently (v1 rule; `diag_offer` is
  valid only while pending is `general`).
* **Version skew**: v2 backend + v1 app — `symptom` renders via the buttons
  default (fully functional), `free` renders caption-only, memory chips are
  simply absent, new phases are inert strings. v1 backend + v2 app — no new
  kinds/steps ever arrive; the revised `qQueryType` caption shows the v1 text
  from the wire. Both skews are fully functional.
* **Photo quality (spec §4)**: unchanged existing pipeline — a low-quality
  photo still advances the guide (the photo step completes on
  `photo.upload`), the model comments and may ask for a better one during
  consult; the flow never stops.

END OF CONTRACT (v2). Field names, event names, phase ids, step ids, option
ids, tool names, storage fields and Uzbek strings above are frozen; anything
not specified here is the implementer's choice as long as observable
behaviour matches this document.


---

## Phase 2 — Diagnosis engine (§6 preparations + §5 photo ranking)

ADDENDUM to the v2 contract above. Everything in v1/v2 stands unchanged
unless a row below says otherwise. Two implementers (backend, mobile) build
from this section in parallel — every wire shape, field name, string and
fail-open rule below is FROZEN. §-references inside this addendum use the
`P2.x` numbering; `§n` still means the v2 sections above.

### P2.0 Design summary

Two additions to the diagnosis path, both strictly additive on the wire:

1. **Preparations (Growz Agroapteka lookup)** — after `diagnose()` returns,
   the backend fuzzy-matches `result.likely_disease` against the Growz
   disease/weed catalogue, pulls that disease's treatments
   (`/api/ai/treatments?disease_id=…`), and ships the top preparations as a
   NEW top-level `preparations` array on the existing `case.diagnosis`
   event. `result` stays the raw Gemini `DiagnosisResult` — preparations are
   deliberately a SEPARATE key (different source, different trust level,
   independently fail-open). Rais names the top 1–2 preparations aloud and
   mentions the Growz Agroapteka; the card renders a "Tavsiya etilgan
   preparatlar" section. No buttons, no deep link (Marketplace link deferred).
2. **Photo ranking** — when the farmer sent more than 3 photos, a cheap
   flash call picks the best ≤3 (symptom clarity, focus, damaged-organ
   visibility, non-duplicate angles) before the expensive Pro diagnosis
   call. Invisible on the wire; pure cost/quality optimisation.

Both features obey the fail-open law (P2.10): the farmer ALWAYS gets the
diagnosis they get today, byte-identical when either feature fails.

### P2.1 Wire change — `case.diagnosis` gains top-level `preparations`

The ONLY protocol change in Phase 2. Server → client:

```json
{
  "type": "case.diagnosis",
  "case_id": "case_1",
  "result":  { "...": "DiagnosisResult.model_dump() — UNCHANGED from v1" },
  "summary": { "...": "interview summary — UNCHANGED from v1" },
  "preparations": [
    {
      "name": "NURELL AGRO 55% EM.K",
      "dose_min": 0.75,
      "dose_max": 1.0,
      "unit": "l/ga",
      "type": "pest",
      "description": "Keng taʼsir doirali insektitsid…"
    }
  ]
}
```

**Preparation object — frozen fields (all six keys ALWAYS present):**

| field | JSON type | source (Growz treatment row) | rule |
|-------|-----------|------------------------------|------|
| `name` | string | `drug.name` | non-empty; rows with a missing/empty `drug.name` are DROPPED before de-dup |
| `dose_min` | number \| null | `dose_min`, else `drug.dose_min` | fallback fires only when the ROW value is null/absent; coerced to float; non-numeric → null |
| `dose_max` | number \| null | `dose_max`, else `drug.dose_max` | same rule as `dose_min` (fallbacks are independent per field) |
| `unit` | string | `drug.unit.name` | `""` when absent |
| `type` | string | `type` | lowercased passthrough — `"disease"` \| `"pest"` observed today; `""` when absent; mobile must tolerate ANY string |
| `description` | string | `drug.description` | `""` when absent; `.strip()`, truncated to 300 chars |

Array order = Growz API row order after de-dup; max 4 entries (P2.2). May be
`[]` (no match, weed with no treatments, Growz down, no API key) — the key is
ALWAYS emitted, `[]` in every failure case.

`backend/app/schemas.py` — `CaseDiagnosis` gains one field:

```python
class CaseDiagnosis(BaseModel):
    type: Literal["case.diagnosis"] = "case.diagnosis"
    case_id: str
    result: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    # Phase 2: Growz Agroapteka preparations for result.likely_disease.
    # SEPARATE from result (Gemini verdict vs Growz catalogue lookup); [] on
    # any lookup failure — never blocks the diagnosis. Shape: P2.1 addendum.
    preparations: list[dict[str, Any]] = Field(default_factory=list)
```

**Version skew** (both directions fully functional):
* v2 backend + v1 app — `CaseDiagnosis.fromJson` reads named keys only; the
  extra `preparations` key is ignored. Farmers still HEAR the preparations
  (P2.4 read-aloud).
* v1 backend + v2 app — key absent → parses to `const []` → the card section
  is simply not rendered.

### P2.2 Backend — NEW `backend/app/voice/enrich/treatments.py`

Mirrors `enrich/crops.py` exactly: module-level `_client: httpx.AsyncClient |
None` + lazy `_http()` (timeout 15s, keepalive 2), `x-api-key:
settings.growz_api_key` header, `raise_for_status()`, `except Exception →`
empty result with `logger.warning(..., exc_info=True)`, and an `aclose()`
that closes the client and drops the caches (test/shutdown hygiene — like
`crops.aclose()` it is only invoked from the test autouse fixture today).

```python
logger = logging.getLogger("voice.enrich.treatments")

_cache: dict[str, list[dict]] = {}          # "diseases" | "weeds" -> catalogue
_client: httpx.AsyncClient | None = None

PREP_CAP = 4            # max preparations returned / emitted
TREATMENTS_LIMIT = 25   # limit= param on /api/ai/treatments
CATALOGUE_LIMIT = 5000  # limit= param on /api/ai/diseases|weeds
DESC_MAX = 300          # description truncation (chars)

async def find_preparations(
    settings: Settings, disease_name: str, kind: str = "disease_pest",
) -> list[dict]:
    """Top Growz preparations for a diagnosed disease/weed name.

    NEVER raises — returns [] on any failure, missing key, or no match.
    Returned dicts are the frozen P2.1 Preparation shape.
    """
```

**Catalogue choice (`kind`)** — frozen rule: `kind == "weed"` → cache+use
`GET /api/ai/weeds`; ANY other value (`"disease_pest"`, `"general"`, `""`,
unknown) → `GET /api/ai/diseases`. Both fetched with
`params={"limit": CATALOGUE_LIMIT}`, cached process-wide per endpoint in
`_cache["diseases"]` / `_cache["weeds"]` as
`[{"id","name","biology_name"}]`, rows with empty `id` or `name` dropped.

**Fuzzy match** — the okina rule from `guide.py._find_crop_match`, COPIED
locally (enrich must not import from chat — wrong layering direction):

```python
_OKINA_RE = re.compile(r"[ʻ’']")           # same class as guide.py:223

def _norm(s: str) -> str:
    return _OKINA_RE.sub("'", (s or "").strip().lower())
```

1. **Exact pass**: first catalogue row where `_norm(name) == _norm(disease_name)`
   OR `_norm(biology_name) == _norm(disease_name)` (Gemini sometimes answers
   with the Latin binomial).
2. **Containment pass** (on `name` only): candidates where
   `_norm(disease_name) in _norm(name)` or `_norm(name) in _norm(disease_name)`;
   accepted ONLY if exactly one candidate (the §1.6 single-candidate rule).
3. No match / ambiguous → return `[]` (log at INFO, not warning — an unknown
   disease is normal, not an error).

**Treatments fetch** — `GET {growz_api_url}/api/ai/treatments` with
`params={"disease_id": <matched id>, "limit": TREATMENTS_LIMIT}`. The param
name is `disease_id` EXACTLY — `diseaseId=` / `disease=` are silently
ignored by the API and would return the full 45 982-row table. Used for BOTH
kinds (weed ids ride the same param; an empty `data` → `[]`, silently).

**Build rule** — iterate `data` in API order; skip rows without a non-empty
`drug.name`; map to the P2.1 dict (dose fallback rule per field as in the
P2.1 table); de-dupe by `_norm(name)` keeping the FIRST occurrence; stop
after `PREP_CAP` (4) entries.

### P2.3 Backend wiring — `diagnosis_kind` + `_run_finalize_case`

**`gemini_live.py` `__init__`** (next to `self.last_diagnosis`): new public
attribute

```python
# Phase 2: which Growz catalogue find_preparations uses. Set by
# voice_agent.py from the bound chat's query_type; "disease_pest" for
# plain (chatless) sessions and for "general" chats.
self.diagnosis_kind: str = "disease_pest"
```

**`voice_agent.py`** — inside the EXISTING `if chat_doc is not None and
chat_store is not None:` try-block (the guide-setup try; fails open with
it), one added line:

```python
session.diagnosis_kind = chat_doc.query_type or "disease_pest"
```

(`"weed"` → weeds catalogue; `"disease_pest"`/`"general"`/`""` → diseases,
per P2.2. No hasattr guard needed — the attribute exists on the session.)

**`_run_finalize_case`** — new body order (only the marked lines change):

```python
try:
    photos_for_dx = await select_best_photos(          # NEW (P2.6)
        self._s, self._auth, self._photos, max_n=3)
    result = await diagnose(self._s, self._auth, summary, photos_for_dx)
    try:                                               # NEW (P2.2)
        preparations = await find_preparations(
            self._s, result.likely_disease, self.diagnosis_kind)
    except Exception:  # noqa: BLE001 — belt over an already-fail-open call
        logger.exception("preparations lookup failed")
        preparations = []
    self.last_diagnosis = {
        "disease": result.likely_disease,
        "confidence": result.confidence,
        "date": date.today().isoformat(),
        "preparations": [p["name"] for p in preparations][:3],   # NEW
    }
    await self._send_json({
        "type": "case.diagnosis",
        "case_id": case_id,
        "result": result.model_dump(),
        "summary": summary,
        "preparations": preparations,                  # NEW
    })
    await self._speak_text(_diagnosis_spoken(result, preparations))  # P2.4
except ...  # UNCHANGED v1 failure path (error event + Uzbek apology)
```

Ordering is load-bearing: `find_preparations` runs AFTER `diagnose()` and
inside its own try, so no preparations bug can ever consume a successfully
computed diagnosis; `select_best_photos` never raises (P2.6), so it may sit
inside the outer try. `self._photos` is NEVER mutated — unselected photos
stay in the session (Live already saw them; memory/teardown unaffected).

### P2.4 Read-aloud template (backend-only string; NOT in `strings.dart`)

`_diagnosis_spoken(result, preparations)` — frozen:

* `preparations == []` → BYTE-IDENTICAL to v1:
  `"[TIZIM] Tashxis tayyor. Fermerga shu xulosani qisqa o'qib ber: " + result.spoken_summary`
* otherwise, with `names = " va ".join(p["name"] for p in preparations[:2])`
  (top 1–2, API order):

```python
"[TIZIM] Tashxis tayyor. Fermerga shu xulosani qisqa o'qib ber: "
+ result.spoken_summary
+ f" Soʻngra bir jumlada qoʻshib ayt: davolash uchun {names} kabi "
  "preparatlar bor, ularni Growz Agroaptekasidan olsa boʻladi."
```

The v1 prefix keeps its deployed bytes (plain `'` in `o'qib`); the NEW
sentence uses the proper okina `ʻ` (U+02BB) in `Soʻngra`, `qoʻshib`,
`boʻladi`. Like all `[TIZIM]` scripts (§6) this never appears client-side.

### P2.5 Persistence — `last_diagnosis` + chat message + UZ key

* `session.last_diagnosis` gains `"preparations": list[str]` — drug names
  only, max 3, ALWAYS present (`[]` when none). Additive: the memory
  finalize call passes the dict through opaquely; consumers use `.get()`.
* `chat/models.py` `UZ` gains ONE key (backend-only; NOT mirrored in
  `strings.dart` — the stored text is composed server-side only):

  | key | Uzbek string |
  |-----|--------------|
  | `prepPrefix` | `Preparatlar:` |

* `voice_agent.py` teardown — the stored diagnosis message extends v1's
  `f"{UZ['diagPrefix']} {disease} (ishonch: {confidence})"` with, ONLY when
  `diag.get("preparations")` is non-empty:

  ```python
  f" {UZ['prepPrefix']} " + ", ".join(diag["preparations"])
  ```

  Example stored text:
  `Tashxis: Un shudring (ishonch: high) Preparatlar: TOPAZ 10% EM.K, SKOR`

### P2.6 Backend — NEW `backend/app/voice/pipeline/photo_select.py`

```python
class PhotoRanking(BaseModel):
    chosen: list[int]        # 0-based indices into the photo list, best first

RANKING_TIMEOUT_S = 20.0

async def select_best_photos(
    settings: Settings,
    auth: GoogleAuth,
    photos: list[PhotoAttachment],
    max_n: int = 3,
) -> list[PhotoAttachment]:
    """Pick the <=max_n most diagnostic photos. NEVER raises (CancelledError
    excepted); NEVER mutates ``photos``; fail-open -> photos[:max_n]."""
```

* `len(photos) <= max_n` → return `photos` as-is. NO API call (the common
  case costs nothing).
* Otherwise ONE flash call, mirroring `diagnosis.py`'s genai style:
  `client = auth.genai_client()`; `model=settings.gemini_model` (the flash
  Live model, NOT `diagnosis_model`); one user `Content` whose parts are,
  for each photo `i` in order: `types.Part(text=f"PHOTO {i}")` then
  `types.Part(inline_data=types.Blob(data=p.data, mime_type=p.mime))`;
  config: `response_mime_type="application/json"`,
  `response_schema=PhotoRanking`, `temperature=0.0`,
  `max_output_tokens=256`, `system_instruction=_RANKING_PROMPT.format(max_n=max_n)`.
  The whole call is wrapped in `asyncio.wait_for(..., RANKING_TIMEOUT_S)`.
* `_RANKING_PROMPT` (frozen, `{max_n}` interpolated):

  ```
  You rank a farmer's photos for a plant-disease diagnosis. Each photo is
  preceded by a text label 'PHOTO <index>'. Choose the photos that together
  give a plant pathologist the most information: (1) symptoms clearly
  visible and in focus; (2) the damaged organ (leaf, stem, fruit, root)
  fills the frame; (3) prefer different angles or different organs over
  near-duplicates. Return JSON {{"chosen": [<indices>]}} with at most
  {max_n} zero-based indices, best first.
  ```
* Parse: `response.parsed` when it is a `PhotoRanking`, else
  `PhotoRanking.model_validate_json(response.text)`.
* Validate `chosen`: keep ints `0 <= i < len(photos)`, de-dupe preserving
  first occurrence, truncate to `max_n`. Empty after validation → fallback.
  1..max_n survivors → return `[photos[i] for i in sorted(valid)]`
  (ORIGINAL capture order — deterministic for tests; "best first" order is
  discarded).
* **Fail-open**: ANY `Exception` (network, timeout, MAX_TOKENS, unparsable
  JSON, empty `chosen`) → `return photos[:max_n]` after
  `logger.warning("photo ranking failed — using first %d", max_n,
  exc_info=True)`. `asyncio.CancelledError` re-raises.

### P2.7 Mobile — `Preparation` model + `CaseDiagnosis` parse (`events.dart`)

New model in the "Nested diagnosis models" block (after `DiagnosisResult`):

```dart
/// One recommended preparation from the Growz Agroapteka catalogue,
/// riding `case.diagnosis.preparations` (contract addendum P2.1).
class Preparation {
  const Preparation({
    required this.name,
    required this.doseMin,
    required this.doseMax,
    required this.unit,
    required this.type,
    required this.description,
  });

  final String name;
  final double? doseMin; // null when Growz has no dose
  final double? doseMax;
  final String unit;     // e.g. 'l/ga'; '' when absent
  final String type;     // 'disease' | 'pest' | 'weed' | '' | anything else
  final String description;

  factory Preparation.fromJson(Map<String, dynamic> json) => Preparation(
        name: _str(json['name']),
        doseMin: _numOrNull(json['dose_min']),
        doseMax: _numOrNull(json['dose_max']),
        unit: _str(json['unit']),
        type: _str(json['type']),
        description: _str(json['description']),
      );
}
```

New helper next to `_str`/`_bool` at the bottom of the file:

```dart
double? _numOrNull(dynamic v) =>
    v is num ? v.toDouble() : (v is String ? double.tryParse(v) : null);
```

`CaseDiagnosis` gains a defaulted field (absent key → `const []`, so a v1
backend parses cleanly):

```dart
  const CaseDiagnosis({
    required this.caseId,
    required this.result,
    required this.summary,
    this.preparations = const [],
  });
  final List<Preparation> preparations;
  // fromJson adds:
  //   preparations: _list(json['preparations'])
  //       .map((e) => Preparation.fromJson(_map(e)))
  //       .toList(growable: false),
```

**Plumbing (exact seams):**
* `lib/features/interview/transcript_provider.dart` —
  `DiagnosisCardBubble` gains `final List<Preparation> preparations;`
  (ctor param, default `const []`); `addDiagnosis` becomes
  `void addDiagnosis(String caseId, DiagnosisResult result, {List<Preparation> preparations = const []})`.
* `lib/features/session/voice_session_controller.dart` (~:452) —
  `case CaseDiagnosis(:final caseId, :final result, :final preparations):
  transcript.addDiagnosis(caseId, result, preparations: preparations);`
* `lib/features/interview/interview_screen.dart` (~:437) —
  `case DiagnosisCardBubble(:final result, :final preparations):
  return DiagnosisCard(result: result, preparations: preparations);`

### P2.8 Mobile — `diagnosis_card.dart` preparations section

`DiagnosisCard` gains `final List<Preparation> preparations;` (ctor param,
default `const []`). Rendered ONLY on the full-card path (never inside
`_NotAPlantCard`), AFTER the `Oldini olish` `_BulletSection` and before the
trailing `SizedBox(height: 4)`, ONLY when `preparations.isNotEmpty`.

Layout (a plain always-visible `Column`, NOT an `ExpansionTile` — the
preparations are the payoff of the diagnosis):

1. **Section header** — `Padding(fromLTRB(14, 4, 14, 8))`: `Row` of
   `Icon(Icons.medication_outlined, size: 20, color: theme.colorScheme.primary)`,
   8px gap, `Text('Tavsiya etilgan preparatlar',
   style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w600))`.
2. **One tile per preparation** — `Container` with
   `margin: EdgeInsets.fromLTRB(14, 0, 14, 8)`, `padding: EdgeInsets.all(10)`,
   `decoration: BoxDecoration(color: theme.colorScheme.surfaceContainerHighest
   .withValues(alpha: 0.5), borderRadius: BorderRadius.circular(10))`:
   * Row: `Expanded(Text(p.name, style: bodyMedium w600))` + (when the type
     maps) an 8px gap and a type badge — REUSE `_ConfidenceChip(label, color)`:

     | `p.type` | badge label | color |
     |----------|-------------|-------|
     | `disease` | `Kasallik` | `Colors.deepOrange` |
     | `pest` | `Zararkunanda` | `Colors.brown` |
     | `weed` | `Begona oʻt` | `Colors.teal` |
     | anything else / `''` | NO badge | — |
   * Dose line, only when `doseMin != null || doseMax != null`
     (`bodySmall`, `color: theme.colorScheme.outline`, 4px top gap):
     `'Doza: ' + _fmtDose(p)` where `_fmtDose` =
     `min–max` (U+2013 en dash) when both non-null and different; the single
     value when equal or only one is non-null; then `' ' + unit` appended
     when `unit` is non-empty. Numbers via `_fmtNum(double v)`: integer-valued
     → no decimals (`1`), else trailing-zero-trimmed up to 2 decimals
     (`0.75`, `0.5`). Example: `Doza: 0.75–1 l/ga`.
   * Description, only when non-empty (`bodySmall`, `maxLines: 2`,
     `overflow: TextOverflow.ellipsis`, 4px top gap).
3. **Agroapteka footer** — `Padding(fromLTRB(14, 0, 14, 10))`:
   `Text('Growz Agroaptekasidan xarid qilishingiz mumkin.',
   style: theme.textTheme.bodySmall?.copyWith(color:
   theme.colorScheme.outline))`. NO button, NO link, NO tap handler
   (Marketplace deep-link is deferred — there is no URL yet).

**Frozen Uzbek strings (inline literals in `diagnosis_card.dart`, matching
the card's existing convention — NOT added to `features/chat/strings.dart`;
`ʻ` is U+02BB):**

| string | where |
|--------|-------|
| `Tavsiya etilgan preparatlar` | section header |
| `Doza: ` | dose label prefix (trailing space) |
| `Kasallik` | `type == 'disease'` badge |
| `Zararkunanda` | `type == 'pest'` badge |
| `Begona oʻt` | `type == 'weed'` badge |
| `Growz Agroaptekasidan xarid qilishingiz mumkin.` | footer line |

### P2.9 File-by-file change list (Phase 2)

#### BACKEND FILES

| file | change |
|------|--------|
| `backend/app/voice/enrich/treatments.py` | NEW — cached fail-open Growz client, okina fuzzy match, `find_preparations` (P2.2), `aclose()`. |
| `backend/app/voice/pipeline/photo_select.py` | NEW — `PhotoRanking`, `_RANKING_PROMPT`, `select_best_photos` (P2.6). |
| `backend/app/voice/providers/gemini_live.py` | `__init__`: `self.diagnosis_kind = "disease_pest"`. `_run_finalize_case`: P2.3 body (rank → diagnose → guarded preparations → extended `last_diagnosis` → event with `preparations` → P2.4 spoken line via `_diagnosis_spoken`). |
| `backend/app/schemas.py` | `CaseDiagnosis` + `preparations: list[dict[str, Any]]` (P2.1). |
| `backend/app/voice/pipeline/voice_agent.py` | Guide-setup try: `session.diagnosis_kind = chat_doc.query_type or "disease_pest"`. Teardown: append `prepPrefix` suffix to the stored diagnosis message when names exist (P2.5). |
| `backend/app/voice/chat/models.py` | `UZ` + `prepPrefix: "Preparatlar:"` (P2.5). |
| `backend/app/config.py`, `backend/app/main.py`, `backend/app/voice/chat/guide.py`, `backend/app/voice/chat/store.py`, `backend/app/voice/pipeline/diagnosis.py`, `backend/app/voice/pipeline/tools.py` | NO changes (`diagnose()` signature already takes the photo list; no new settings — reuses `growz_api_url`/`growz_api_key`/`gemini_model`). |
| `backend/app/voice/tests/test_enrich_treatments.py` | NEW — `httpx.MockTransport` via `monkeypatch.setattr(treatments, "_client", ...)` + autouse `aclose()` reset (mirror `test_enrich_crops.py`): exact match, okina-variant match, single-containment match, ambiguous → `[]`, no match → `[]`; asserts the request used `disease_id=` exactly; `kind="weed"` hits `/api/ai/weeds`; de-dupe by drug name; cap 4; dose fallback row→drug; description truncation; fail-open on 500 / network error / missing key. |
| `backend/app/voice/tests/test_photo_select.py` | NEW — fake genai client injection (mirror `test_diagnosis.py`): `len<=max_n` passthrough w/o API call; valid ranking selects + returns original order; out-of-range/duplicate indices dropped; empty/garbage response → first `max_n`; raised exception → first `max_n`; input list never mutated. |
| `backend/app/voice/tests/test_voice_agent_chat_wiring.py` | EXTEND — `session.diagnosis_kind` set from `chat_doc.query_type` (weed chat → `"weed"`; chatless session keeps `"disease_pest"`); teardown message carries the `Preparatlar:` suffix iff `last_diagnosis["preparations"]` non-empty. |

#### MOBILE FILES (all under `mobile/`)

| file | change |
|------|--------|
| `lib/core/protocol/events.dart` | `Preparation` model + `_numOrNull` helper; `CaseDiagnosis.preparations` with `const []` default (P2.7). |
| `lib/features/interview/transcript_provider.dart` | `DiagnosisCardBubble.preparations` (default `const []`); `addDiagnosis(..., {preparations})` (P2.7). |
| `lib/features/session/voice_session_controller.dart` | Pass `preparations` through at the `CaseDiagnosis` case (~:452). |
| `lib/features/interview/interview_screen.dart` | Pass `preparations` to `DiagnosisCard` at the bubble case (~:437). |
| `lib/features/diagnosis/diagnosis_card.dart` | `preparations` param + section per P2.8 (header, tiles with badge/dose/description, Agroapteka footer, frozen strings). |
| `test/events_test.dart` | EXTEND — `case.diagnosis` round-trip WITH `preparations` (full object, string-typed doses coerce, unknown `type` tolerated) and WITHOUT the key (→ empty list). |
| `lib/features/chat/strings.dart`, camera/avatar/chat files | NO changes. |

Infra: NO changes.

### P2.10 FAIL-OPEN law (Phase 2 — on top of all v1/v2 rules)

A preparations or ranking failure NEVER breaks, delays past its timeout, or
degrades the diagnosis the farmer gets today.

* **Preparations**: `find_preparations` NEVER raises — `[]` on missing key,
  network error, non-200, bad shape, no/ambiguous catalogue match, zero
  treatments. The call site wraps it in its own try → `[]` anyway (belt
  over braces), and it runs AFTER `diagnose()`, so a computed diagnosis can
  never be lost to a lookup bug. With `[]`: the event ships
  `"preparations": []`, the card hides the section, the read-aloud is
  byte-identical to v1, the stored chat message has no suffix.
* **Photo ranking**: `select_best_photos` NEVER raises (CancelledError
  excepted) and NEVER mutates `self._photos`; any error/timeout/garbage →
  `photos[:max_n]` (the first-N photos the farmer sent — exactly the v1
  photo *count* cap behaviour). `len(photos) <= max_n` skips the API call
  entirely: zero added latency or cost for the common case.
* **Chatless / general sessions**: `diagnosis_kind` defaults to
  `"disease_pest"` → diseases catalogue; a weed-chat lookup that finds no
  treatments returns `[]` silently.
* **Version skew**: v2 backend + v1 app — extra `preparations` key ignored
  by the parser; the farmer still hears the preparations spoken. v1 backend
  + v2 app — missing key parses to `[]`; section hidden. Both skews fully
  functional (P2.1).
* **Persistence**: the new `last_diagnosis["preparations"]` key is additive;
  memory extraction and chat teardown read the dict with `.get()` and
  tolerate its absence AND its presence.

END OF PHASE 2 ADDENDUM. Field names, the Preparation object shape, catalogue
and match rules, caps (PREP_CAP=4, max_n=3, TREATMENTS_LIMIT=25,
CATALOGUE_LIMIT=5000, DESC_MAX=300), the ranking response schema, the spoken
template and every Uzbek string above are frozen; anything not specified is
the implementer's choice as long as observable behaviour matches.


---

## Phase 3 — §7 Agronom verification (stub)

ADDENDUM to the v2 contract + Phase 2 addendum above. Everything in v1/v2/P2
stands unchanged unless a row below says otherwise. Two implementers
(backend, mobile) build from THIS SECTION ONLY, in parallel — every wire
shape, field name, status value, Uzbek string and fail-open rule below is
EXACT and FROZEN. `P3.x` references this addendum; `P2.x`/`§n` refer to the
sections above.

### P3.0 Design summary + confirmed stub decisions

Spec §7: after a diagnosis (a `case.diagnosis` with `preparations`) the
farmer may have the answer checked by an agronom.

* **§7 offer** (farmer-facing meaning, delivered by Rais's voice AND an
  on-screen button): «Xohlasangiz, bu javobni agronomga tekshirtirib
  beraman. Agronom diagnoz, preparatlar roʻyxati va dozalarni koʻrib chiqib,
  aniqroq tavsiya beradi.»
* **§7.1** «Agronomga yuborish» → a review request is created (`pending`).
  «Hozir emas» is IMPLICIT — no button, no tool, no event; the farmer simply
  does not tap / does not agree, and nothing happens.
* **§7.2** when the answer is ready: the AI answer and the EXPERT answer are
  shown as SEPARATE cards, with an updated preparations list and the badge
  «Agronom tasdiqlagan javob».

Confirmed stub decisions (build EXACTLY this):

1. **Two expert sources, BOTH built.** (a) A REAL submit endpoint a human
   agronom / staff calls (P3.4, token-guarded). (b) An AUTO "senior agronom"
   AI second opinion (P3.5) kicked off by the farmer's request when
   `agronom_mock_enabled` is on — ALWAYS stored with `is_mock: true` and
   rendered with the «AI yordamchi (sinov)» label, so it is visibly a
   placeholder. Swap-out path: real agronoms later just use (a); a human
   submit OVERWRITES a mock review (P3.4/P3.5 concurrency rule).
2. **Delivery = poll-on-open.** The review surfaces via the EXISTING
   `GET /chats` / `GET /chats/{id}` fetches when the farmer reopens the chat
   list or a chat. NO FCM / push now. The `status` field
   (`"none" | "pending" | "done"` + `requested_at`/`reviewed_at`) is designed
   so a future push worker only has to watch `pending → done` transitions —
   no schema change will be needed.
3. **Fail-open, OFF by default.** Master flag `agronom_enabled = False`
   (P3.9). With the flag off the backend is byte-identical to Phase 2. A
   broken, slow, or failed review NEVER touches the diagnosis, the live
   call, or the existing endpoints (P3.11 law).
4. **Chat-bound.** The review attaches to the SAME `ChatDoc` (by `chat_id`)
   whose teardown stores the diagnosis (§4.7 / P2.5), so it shows in
   history. Chatless (plain) voice sessions get NO voice offer and NO button
   — there is nowhere to store or re-surface a review.
5. **No new WS events.** Everything rides REST + the existing chat
   summary/detail payloads. `mobile/lib/core/protocol/events.dart` does not
   change.

### P3.1 Storage — `AgronomReview` on the `ChatDoc`

**`backend/app/voice/chat/models.py`** — new model + one `ChatDoc` field:

```python
class AgronomReview(BaseModel):
    """Spec §7 agronom verification (contract Phase 3). Lives on the chat
    document; surfaced verbatim in build_summary/build_detail."""
    status: str = "none"          # "none" | "pending" | "done"
    requested_at: str = ""        # now_iso() when the farmer requested
    reviewed_at: str = ""         # now_iso() when the review landed
    is_mock: bool = False         # True = AI second-opinion stub (P3.5)
    verdict: str = ""             # "" | "confirmed" | "adjusted"
    expert_summary: str = ""      # Uzbek, farmer-facing, <=600 chars
    expert_notes: list[str] = Field(default_factory=list)   # <=6 x <=300
    # SAME frozen P2.1 Preparation dict shape (all six keys). [] means the
    # AI preparations list stands unchanged.
    adjusted_preparations: list[dict] = Field(default_factory=list)


class ChatDoc(BaseModel):
    ...                                        # all v2/P2 fields UNCHANGED
    agronom_review: AgronomReview | None = None   # NEW in Phase 3
```

Old on-disk docs load fine (default `None`). Exported from
`app/voice/chat/__init__.py` alongside `ChatDoc`.

**Caps + sanitizer (models.py, pure function — shared by the human endpoint
AND the mock runner; frozen rules):**

```python
_AGRONOM_SUMMARY_MAX = 600
_AGRONOM_NOTE_MAX = 300
_AGRONOM_NOTES_CAP = 6
_AGRONOM_PREP_CAP = 4          # same cap as P2 PREP_CAP

def sanitize_expert_payload(
    expert_summary: object,
    expert_notes: object,
    adjusted_preparations: object,
) -> tuple[str, list[str], list[dict]]:
    """Clamp untrusted expert fields to the frozen shape. Never raises."""
```

* `expert_summary` → `str(...).strip()[:600]`.
* `expert_notes` → for each item: `str(...).strip()`, drop empties, truncate
  each to 300, keep the FIRST 6.
* `adjusted_preparations` → coerce each item to the frozen P2.1 Preparation
  dict (all six keys always present): `name` = stripped string, item DROPPED
  when empty; `dose_min`/`dose_max` float-coerced (non-numeric → `null`);
  `unit` string default `""`; `type` lowercased string default `""`;
  `description` stripped string `[:300]`. Keep the FIRST 4 surviving items.
  Non-list / non-dict input → `[]`.

**`build_summary` AND `build_detail` both gain ONE key (always emitted):**

```python
"agronom_review": (
    doc.agronom_review.model_dump() if doc.agronom_review is not None else None
),
```

Wire example (inside any chat summary or `GET /chats/{id}` detail):

```json
"agronom_review": {
  "status": "done",
  "requested_at": "2026-07-14T09:12:31+00:00",
  "reviewed_at": "2026-07-14T09:13:02+00:00",
  "is_mock": true,
  "verdict": "adjusted",
  "expert_summary": "Tashxis toʻgʻri, ammo doza pasaytirilishi kerak.",
  "expert_notes": ["Ertalab salqinda ishlov bering.", "7 kundan keyin takrorlang."],
  "adjusted_preparations": [
    {"name": "TOPAZ 10% EM.K", "dose_min": 0.3, "dose_max": 0.5,
     "unit": "l/ga", "type": "disease", "description": "…"}
  ]
}
```

`null` when never requested. An old (Phase-2) app ignores the unknown key —
fully backward compatible.

**`backend/app/voice/chat/store.py` — the ONE store change (load-bearing):**

`ChatStore.save()` gains a preservation rule, inside the existing try:
BEFORE writing, if `doc.agronom_review is None`, read the currently stored
file and, when it parses and carries a non-null `agronom_review`, copy that
object onto `doc`. Rationale: the live voice session and its guide hold
their own in-memory `ChatDoc` from connect time; a mid-call REST
`agronom-request` (P3.3) writes `pending` to disk, and WITHOUT this rule the
guide's next `append_message`/teardown save would silently clobber it.
Frozen invariant: **a writer that never saw a review can never erase one**
(clearing a review is not a supported operation anywhere). Fail-open: any
read/parse error during the merge → proceed with the plain write (old
behaviour). Writers that DO carry a non-None `agronom_review` (the two REST
endpoints, the mock runner — all of which read fresh under `lock_for(chat_id)`)
write it through unchanged.

**Chat message on review completion** (both sources): one stored message via
`store.append_message(doc, "agronom", "agronom_review",
f"{UZ['agronomPrefix']} {expert_summary}")`. `ChatMessage.role` vocabulary
widens to `farmer | rais | system | agronom`; `kind` gains
`agronom_review`. An old app renders it as a plain system bubble (its
`addHistory` default branch) — graceful. `derive_title` is unaffected (its
farmer-message rules never match role `agronom`).

### P3.2 Diagnosis persistence — 3 additive `last_diagnosis` keys

The reviewer needs the FULL AI verdict, and today only the thin
`{disease, confidence, date, preparations(names)}` dict is persisted
(P2.5). `gemini_live.py` `_run_finalize_case` therefore extends
`self.last_diagnosis` with three ADDITIVE keys (P2.5 already froze the
tolerance rule: every consumer uses `.get()`):

```python
self.last_diagnosis = {
    "disease": result.likely_disease,                      # P2, unchanged
    "confidence": result.confidence,                       # P2, unchanged
    "date": date.today().isoformat(),                      # P2, unchanged
    "preparations": [p["name"] for p in preparations][:3], # P2, unchanged
    "result": result.model_dump(),        # NEW P3 — full DiagnosisResult
    "summary": summary,                   # NEW P3 — interview summary dict
    "preparations_full": preparations,    # NEW P3 — full P2.1 dicts (<=4)
}
```

The teardown already copies this dict onto `chat_doc.last_diagnosis`
(§4.7) — no teardown change for this. Memory finalize reads only
`disease`/`date` (verified) — unaffected. Mobile reads `last_diagnosis` as
an opaque map — unaffected. Old chats (thin dict) still review fine via the
P3.5 fallback input rule.

### P3.3 REST — `POST /chats/{chat_id}/agronom-request` (farmer)

Same trust level as the other `/chats` routes (device-id scoping; the nginx
`^~ /chats` carve-out already exposes it without Basic Auth — no infra
change). Body model at module scope in `main.py` (same rationale as
`_CreateChatBody`):

```python
class _AgronomRequestBody(BaseModel):
    user_id: str = ""
```

Request:

```
POST /chats/{chat_id}/agronom-request
Content-Type: application/json

{"user_id": "<device-id>"}
```

Handler (exact semantics):

1. `settings.agronom_enabled` false → `404 {"detail": "agronom not available"}`.
2. `valid_device_id(user_id)` false → `400 {"detail": "invalid user_id"}`.
3. `async with lock_for(chat_id):` read the chat (`ChatStore.read`); `None`
   or `doc.user_id != user_id` → `404 {"detail": "chat not found"}` (never
   reveal other owners' chats — v1 rule).
4. `doc.agronom_review is None` → set
   `doc.agronom_review = AgronomReview(status="pending", requested_at=now_iso())`,
   bump `doc.updated_at = now_iso()`, `store.save(doc)`, and log ONE
   WARNING-level breadcrumb (the stub's "queue" for human agronoms —
   container surfaces warnings+):
   `logger.warning("agronom review requested: user=%s chat=%s crop=%s disease=%s", user_id, chat_id, doc.crop_name, (doc.last_diagnosis or {}).get("disease", ""))`.
5. Already `pending` or `done` → NO change (idempotent; timestamps and any
   finished review preserved).
6. After the lock: `maybe_start_mock_review(settings, store, doc)` (P3.5 —
   internally flag-gated, fire-and-forget, fail-open).
7. Response `200`:

```json
{"data": { …chat summary (P2 shape)…, "agronom_review": {
  "status": "pending",
  "requested_at": "2026-07-14T09:12:31+00:00",
  "reviewed_at": "", "is_mock": false, "verdict": "",
  "expert_summary": "", "expert_notes": [], "adjusted_preparations": []
}}}
```

NO diagnosis precondition: a mid-call request lands BEFORE teardown persists
`last_diagnosis`, so the endpoint accepts and stores `pending` regardless;
the mock kicks in later from teardown (P3.5 seams). A chat that never gets a
diagnosis simply stays `pending` until a human submits — harmless.

The farmer reads status/result via the EXISTING `GET /chats/{chat_id}` (and
the list) — no new read endpoint.

### P3.4 REST — `POST /chats/{chat_id}/agronom-review` (human/staff submit)

**Auth — precise:** header `X-Agronom-Token: <token>`, compared against
`settings.agronom_review_token` with `secrets.compare_digest`. The token
travels ONLY in the header (never the body, never the URL — this route sits
in the unauthenticated `/chats` nginx carve-out, so the token IS the gate).
When `agronom_review_token` is `""` (the default) the endpoint is dead:
`404` — it can never be open by accident.

Body model (module scope, `main.py`):

```python
class _AgronomReviewBody(BaseModel):
    user_id: str = ""
    verdict: str = ""
    expert_summary: str = ""
    expert_notes: list[str] = Field(default_factory=list)
    adjusted_preparations: list[dict] = Field(default_factory=list)
```

`user_id` is REQUIRED (the file store is keyed
`data/chats/<user_id>/<chat_id>.json`); staff take both ids from the P3.3
WARNING log line. (A pending-review queue endpoint is DEFERRED along with
push.)

Request:

```
POST /chats/{chat_id}/agronom-review
Content-Type: application/json
X-Agronom-Token: <AGRONOM_REVIEW_TOKEN>

{
  "user_id": "a1b2c3…-device-id",
  "verdict": "adjusted",
  "expert_summary": "Tashxis toʻgʻri, ammo doza pasaytirilishi kerak.",
  "expert_notes": ["Ertalab salqinda ishlov bering."],
  "adjusted_preparations": [
    {"name": "TOPAZ 10% EM.K", "dose_min": 0.3, "dose_max": 0.5,
     "unit": "l/ga", "type": "disease", "description": ""}
  ]
}
```

Validation order (frozen):

| # | condition | response |
|---|-----------|----------|
| 1 | `agronom_enabled` false OR `agronom_review_token == ""` | `404 {"detail": "agronom not available"}` |
| 2 | header missing OR `compare_digest` mismatch | `401 {"detail": "invalid token"}` |
| 3 | invalid `user_id` | `400 {"detail": "invalid user_id"}` |
| 4 | chat missing / other owner | `404 {"detail": "chat not found"}` |
| 5 | `doc.agronom_review is None` (never requested) | `409 {"detail": "review not requested"}` |
| 6 | `verdict not in ("confirmed", "adjusted")` | `400 {"detail": "invalid verdict"}` |
| 7 | `expert_summary` empty after strip | `400 {"detail": "empty expert_summary"}` |

Accepted while status is `pending` OR `done` — a human review ALWAYS
overwrites, including a finished mock (`is_mock` flips to `false`); that is
the swap-out path for real agronoms. Write, under `lock_for(chat_id)` on a
fresh read:

```python
summary, notes, preps = sanitize_expert_payload(
    body.expert_summary, body.expert_notes, body.adjusted_preparations)
r = doc.agronom_review
r.status = "done"; r.reviewed_at = now_iso(); r.is_mock = False
r.verdict = body.verdict; r.expert_summary = summary
r.expert_notes = notes; r.adjusted_preparations = preps
if not r.requested_at:
    r.requested_at = now_iso()
store.append_message(doc, "agronom", "agronom_review",
                     f"{UZ['agronomPrefix']} {summary}")   # bumps updated_at + saves
```

Response `200 {"data": build_summary(doc)}` (carries the finished
`agronom_review`). Staff example:

```bash
curl -X POST "https://<host>/chats/<chat_id>/agronom-review" \
  -H 'Content-Type: application/json' \
  -H "X-Agronom-Token: $AGRONOM_REVIEW_TOKEN" \
  -d '{"user_id":"<device-id>","verdict":"confirmed",
       "expert_summary":"Tashxis va preparatlar toʻgʻri.",
       "expert_notes":[],"adjusted_preparations":[]}'
```

### P3.5 AI senior-agronom reviewer — NEW `backend/app/voice/agronom/review.py`

New package `backend/app/voice/agronom/` (`__init__.py` empty or
re-exporting). Mirrors `pipeline/diagnosis.py`'s genai pattern exactly
(`client.aio.models.generate_content`, `response_schema`, MAX_TOKENS
finish-reason check, `response.parsed` → `model_validate_json` fallback).
This is a clearly-labelled STUB second opinion — `is_mock: true` on
everything it writes.

```python
logger = logging.getLogger("voice.agronom")

REVIEW_TIMEOUT_S = 60.0

class ExpertReview(BaseModel):
    verdict: Literal["confirmed", "adjusted"]
    expert_summary: str          # 1-3 short Uzbek sentences, farmer-facing
    expert_notes: list[str]      # 0-6 short practical Uzbek notes
    keep_preparations: bool      # True = the AI list stands
    # When keep_preparations is False: the id of the better-matching entry
    # from the Growz candidate list, or "" (then the AI list stands anyway).
    adjusted_growz_disease_id: str = ""
```

**System prompt (frozen intent, exact text):**

```python
AGRONOM_REVIEW_SYSTEM_PROMPT = (
    "You are a SENIOR agronomist giving a second opinion on a junior AI "
    "assistant's crop diagnosis for a smallholder farmer. Input is a JSON "
    "with the crop, the interview facts, the AI diagnosis (disease, "
    "confidence, differentials, treatment, prevention) and the recommended "
    "preparations with doses.\n"
    "Review it like an expert: is the diagnosis plausible given the facts? "
    "Are the preparations and doses appropriate for this crop and problem?\n"
    "- If the diagnosis and preparations are sound: verdict='confirmed'.\n"
    "- If anything should change (different likely disease, wrong or "
    "missing preparation, dose concern, missing precaution): "
    "verdict='adjusted' and explain in the notes.\n"
    "Set keep_preparations=false ONLY when a DIFFERENT disease from the "
    "GROWZ DISEASE CANDIDATES list fits better — then put its id in "
    "adjusted_growz_disease_id. Otherwise keep_preparations=true and "
    "adjusted_growz_disease_id=\"\".\n"
    "Write expert_summary in UZBEK (Latin script), 1-3 short, simple, "
    "farmer-friendly sentences. expert_notes: 0-6 short practical Uzbek "
    "notes (dose timing, safety, application tips, what to re-check). "
    "Never mention being an AI or that you are reviewing an AI."
)
```

plus a local copy of the `_candidate_block(growz_diseases)` builder from
`diagnosis.py` (same `id: name` listing, same "single best-matching entry"
wording adapted to `adjusted_growz_disease_id`; do NOT import the private
helper).

**Call:**

```python
async def review_diagnosis(
    settings, auth, review_input: dict, growz_candidates: list[dict],
) -> ExpertReview:
    # model=settings.diagnosis_model or settings.gemini_model
    # contents=[Content(role="user",
    #   parts=[Part(text=json.dumps(review_input, ensure_ascii=False))])]
    # config: system_instruction=AGRONOM_REVIEW_SYSTEM_PROMPT + candidates,
    #   response_mime_type="application/json", response_schema=ExpertReview,
    #   temperature=settings.diagnosis_temperature,
    #   max_output_tokens=settings.diagnosis_max_tokens
    # MAX_TOKENS finish check → RuntimeError (caught by the runner)
```

**Review input (exact builder — tolerates thin/old `last_diagnosis`):**

```python
def _review_input(doc: ChatDoc) -> dict:
    diag = doc.last_diagnosis or {}
    return {
        "crop_name": doc.crop_name,
        "plant_part": doc.plant_part,
        "query_type": doc.query_type,
        "symptom_summary": doc.symptom_summary,
        "general_question": doc.general_question,
        "interview_summary": diag.get("summary") or {},
        "ai_diagnosis": diag.get("result") or {
            "likely_disease": diag.get("disease", ""),
            "confidence": diag.get("confidence", ""),
        },
        "ai_preparations": diag.get("preparations_full")
            or [{"name": n} for n in (diag.get("preparations") or [])],
    }
```

**Kickoff seam (SYNC, fire-and-forget; called from exactly TWO places):**

```python
def maybe_start_mock_review(settings, store: ChatStore, doc: ChatDoc) -> None:
    """Start the AI second opinion iff flags on, a request is pending and a
    diagnosis exists to review. Never raises; never blocks the caller."""
    try:
        if not (settings.agronom_enabled and settings.agronom_mock_enabled):
            return
        r = doc.agronom_review
        if r is None or r.status != "pending":
            return
        if not (doc.last_diagnosis or {}).get("disease"):
            return   # nothing to review yet — the teardown call retries
        asyncio.create_task(_run_mock_review(settings, store, doc.user_id, doc.id))
    except Exception:  # noqa: BLE001
        logger.exception("agronom mock kickoff failed")
```

Call sites:
1. `main.py` `agronom-request` handler, after the lock releases (P3.3 step 6)
   — covers post-call requests (diagnosis already on disk).
2. `voice_agent.py` teardown, in a NEW separate try-block AFTER the existing
   chat-finalize try (so an agronom bug can never break the diagnosis
   persist) and BEFORE memory finalize; it must pass the FRESH doc (the
   in-memory one predates any mid-call request):

```python
if chat_store is not None and chat_doc is not None:
    try:
        from app.voice.agronom.review import maybe_start_mock_review
        fresh = chat_store.read(chat_doc.user_id, chat_doc.id)
        if fresh is not None:
            maybe_start_mock_review(settings, chat_store, fresh)
    except Exception:  # noqa: BLE001
        logger.exception("agronom teardown kickoff failed")
```

This pair covers both timings deterministically: request-during-call (mock
starts at teardown, when the diagnosis is persisted) and
request-after-call/from-history (mock starts immediately).

**Runner (frozen flow — FAIL-OPEN: any error leaves status `"pending"`):**

```python
async def _run_mock_review(settings, store, user_id, chat_id) -> None:
    try:
        doc = store.read(user_id, chat_id)
        if doc is None or doc.agronom_review is None: return
        if doc.agronom_review.status != "pending": return
        candidates = await get_crop_diseases(          # fail-open [] (P2.2)
            settings, doc.crop_name, doc.query_type or "disease_pest")
        review = await asyncio.wait_for(
            review_diagnosis(settings, build_auth(settings),
                             _review_input(doc), candidates),
            REVIEW_TIMEOUT_S)
        adjusted: list[dict] = []
        if not review.keep_preparations and review.adjusted_growz_disease_id:
            adjusted = await find_preparations_by_id(   # fail-open [] (P2.2)
                settings, review.adjusted_growz_disease_id)
        summary, notes, adjusted = sanitize_expert_payload(
            review.expert_summary, review.expert_notes, adjusted)
        if not summary: return                          # useless review → stay pending
        async with lock_for(chat_id):
            doc = store.read(user_id, chat_id)          # FRESH under the lock
            if doc is None or doc.agronom_review is None: return
            if doc.agronom_review.status == "done": return   # human beat us — human wins
            r = doc.agronom_review
            r.status = "done"; r.reviewed_at = now_iso(); r.is_mock = True
            r.verdict = review.verdict; r.expert_summary = summary
            r.expert_notes = notes; r.adjusted_preparations = adjusted
            store.append_message(doc, "agronom", "agronom_review",
                                 f"{UZ['agronomPrefix']} {summary}")
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — the review is optional decoration
        logger.warning("mock agronom review failed — stays pending", exc_info=True)
```

Catalogue kind rule = P2.3 (`"weed"` → weeds, anything else → diseases).
Concurrency (frozen): **the human always wins** — mock finishing after a
human submit drops itself (`status == "done"` check under the lock); a human
submit after a mock overwrites it (P3.4).

### P3.6 Where the offer happens

**(a) Rais voice — `gemini_live.py`.** `GeminiLiveSession.__init__` gains a
public attribute (next to `diagnosis_kind`):

```python
# Phase 3: speak the agronom-check offer after the diagnosis read-aloud.
# Set by voice_agent.py only for chat-bound sessions with agronom_enabled.
self.agronom_offer: bool = False
```

`voice_agent.py` sets it inside the EXISTING chat-setup try-block (right
next to the `diagnosis_kind` line — fails open with it):

```python
session.agronom_offer = bool(getattr(settings, "agronom_enabled", False))
```

`_diagnosis_spoken` gains a keyword param with a default that keeps every
existing call/test BYTE-IDENTICAL (the P2.4 freeze holds whenever
`offer_agronom=False`):

```python
_AGRONOM_OFFER_SPOKEN = (
    " Oxirida yana bir jumlada qoʻshib ayt: xohlasa, bu javobni agronom "
    "tekshirib beradi — agronom diagnoz, preparatlar roʻyxati va dozalarni "
    "koʻrib chiqib, aniqroq tavsiya beradi; buning uchun ekrandagi "
    "«Agronomga yuborish» tugmasini bossin."
)

def _diagnosis_spoken(result, preparations, *, offer_agronom: bool = False) -> str:
    ...  # v1 base + P2.4 preparations sentence — bytes UNCHANGED
    if offer_agronom:
        out += _AGRONOM_OFFER_SPOKEN     # appended LAST, after everything
    return out
```

`_run_finalize_case` calls
`_diagnosis_spoken(result, preparations, offer_agronom=self.agronom_offer)`.
The suffix is a backend-only `[TIZIM]`-family string (okina `ʻ` U+02BB; NOT
in `strings.dart`). «Hozir emas» is implicit: no tool, no event — if the
farmer declines or says nothing, Rais just continues; the button stays
available on the card.

**(b) Mobile — the «Agronomga yuborish» button** sits on the
`DiagnosisCard`, AFTER the preparations section (P3.7). **How mobile knows
the `chat_id`:** the diagnosis card only appears inside a session, and a
session is chat-bound iff `activeChatProvider` holds a summary with a
non-empty `id` (that exact id was sent in `session.start.chat_id`). Frozen
rule: `chatId = ref.read(activeChatProvider)?.summary.id`; the button
renders ONLY when that id is non-empty. Tapping calls
`POST /chats/{chatId}/agronom-request` via `ChatService` — pure REST, no WS
event.

### P3.7 Mobile — models, service, providers, rendering

**`lib/features/chat/chat.dart`** (add
`import '../../core/protocol/events.dart';` for `Preparation`):

```dart
/// Spec §7 agronom review, mirrored from `agronom_review` on the chat
/// summary/detail (contract Phase 3, P3.1). `null` on the wire → [none].
class AgronomReview {
  const AgronomReview({
    required this.status,        // 'none' | 'pending' | 'done'
    required this.requestedAt,
    required this.reviewedAt,
    required this.isMock,
    required this.verdict,       // '' | 'confirmed' | 'adjusted'
    required this.expertSummary,
    required this.expertNotes,
    required this.adjustedPreparations,
  });

  final String status;
  final DateTime? requestedAt;
  final DateTime? reviewedAt;
  final bool isMock;
  final String verdict;
  final String expertSummary;
  final List<String> expertNotes;
  final List<Preparation> adjustedPreparations;   // [] = AI list stands

  factory AgronomReview.none() => const AgronomReview(
    status: 'none', requestedAt: null, reviewedAt: null, isMock: false,
    verdict: '', expertSummary: '', expertNotes: [],
    adjustedPreparations: []);

  factory AgronomReview.fromJson(Map<String, dynamic>? json) {
    if (json == null) return AgronomReview.none();
    return AgronomReview(
      status: (json['status'] ?? 'none') as String,
      requestedAt: DateTime.tryParse((json['requested_at'] ?? '') as String? ?? ''),
      reviewedAt: DateTime.tryParse((json['reviewed_at'] ?? '') as String? ?? ''),
      isMock: json['is_mock'] == true,
      verdict: (json['verdict'] ?? '') as String,
      expertSummary: (json['expert_summary'] ?? '') as String,
      expertNotes: (json['expert_notes'] as List<dynamic>? ?? const [])
          .map((e) => e.toString()).toList(growable: false),
      adjustedPreparations:
          (json['adjusted_preparations'] as List<dynamic>? ?? const [])
              .map((e) => Preparation.fromJson(e as Map<String, dynamic>))
              .toList(growable: false),
    );
  }
}
```

`ChatSummary` gains `final AgronomReview agronomReview;` — parsed with
`AgronomReview.fromJson(json['agronom_review'] as Map<String, dynamic>?)`;
`ChatSummary.blank()` uses `AgronomReview.none()`. `ChatDetail` inherits it
for free (it reuses `ChatSummary.fromJson`).

**`lib/features/chat/chat_service.dart`** — one new method (same style: 12s
timeout, non-200 throws):

```dart
Future<ChatSummary> requestAgronomReview(String chatId, String userId) async {
  final uri = Uri.parse('$httpBaseUrl/chats/$chatId/agronom-request');
  final resp = await http.post(uri,
        headers: {'Accept': 'application/json',
                  'Content-Type': 'application/json'},
        body: json.encode({'user_id': userId}))
      .timeout(const Duration(seconds: 12));
  if (resp.statusCode != 200) {
    throw Exception('agronom-request HTTP ${resp.statusCode}');
  }
  final body = json.decode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
  return ChatSummary.fromJson(body['data'] as Map<String, dynamic>);
}
```

(The staff submit endpoint has NO mobile client — server-side only.)

**`lib/features/chat/chat_providers.dart`** — one new controller:

```dart
/// The bound chat's agronom review state (contract Phase 3). Seeded from
/// the chat summary on open; updated by a successful request. `null` =
/// nothing known yet (treated as 'none' for a chat-bound session).
class AgronomReviewController extends Notifier<AgronomReview?> {
  @override
  AgronomReview? build() => null;

  void set(AgronomReview review) => state = review;
  void clear() => state = null;

  /// POST /chats/{id}/agronom-request. Returns false on ANY failure
  /// (fail-open — the caller shows S.agronomRequestFailed and nothing else
  /// changes).
  Future<bool> request(String chatId) async {
    try {
      final userId = await getOrCreateDeviceId();
      final summary =
          await const ChatService().requestAgronomReview(chatId, userId);
      state = summary.agronomReview;
      return true;
    } catch (_) {
      return false;
    }
  }
}

final agronomReviewProvider =
    NotifierProvider<AgronomReviewController, AgronomReview?>(
      AgronomReviewController.new,
    );
```

**`lib/features/diagnosis/diagnosis_card.dart`:**

* `DiagnosisCard` gains two params: `this.agronomStatus = ''` and
  `this.onAgronomRequest` (`VoidCallback?`). Frozen status vocabulary at the
  widget: `''` (chatless — render nothing) | `'none'` | `'pending'` |
  `'done'`.
* Rendered on the FULL-card path only (never `_NotAPlantCard`), AFTER the
  preparations section (after prevention when preparations are empty),
  before the trailing `SizedBox(height: 4)`:
  * `'none'` AND `onAgronomRequest != null` →
    `Padding(fromLTRB(14, 4, 14, 10))` wrapping a full-width
    `FilledButton.tonalIcon(icon: Icon(Icons.support_agent), label:
    Text(S.agronomSend), onPressed: onAgronomRequest)`.
  * `'pending'` → `Padding(fromLTRB(14, 4, 14, 10))` with a `Row`:
    16×16 `CircularProgressIndicator(strokeWidth: 2)`, 8px gap,
    `Text(S.agronomPending, style: bodySmall, color: outline)`.
  * `'done'` / `''` → nothing here (the AgronomCard shows separately).
* `_PreparationsSection` is RENAMED public `PreparationsSection` and gains
  `this.title = 'Tavsiya etilgan preparatlar'` (rendering otherwise
  byte-identical; the Agroapteka footer stays unconditional) so the agronom
  card reuses it.

**NEW `lib/features/diagnosis/agronom_card.dart`** — `AgronomCard(review:
AgronomReview)`, VISUALLY DISTINCT from the AI card:

* `Card` with `shape: RoundedRectangleBorder(borderRadius: 12, side:
  BorderSide(color: theme.colorScheme.tertiary, width: 1.4))` — the AI card
  has no border; this one reads as a different author.
* Header band (`Container`, `color: theme.colorScheme.tertiaryContainer`,
  padding 14/10): `Icon(Icons.verified_user, size: 20)` + 8px +
  `Expanded(Text(S.agronomCardTitle, titleSmall w700))`, all in
  `onTertiaryContainer`; when `review.isMock` a trailing small amber pill
  `S.agronomMockLabel` (padding 10/4, `alpha 0.18` bg, `alpha 0.5` border,
  labelSmall w700 — the P2.8 chip visual, local private copy).
* Body (padding 14):
  1. `Wrap(spacing: 6)` of pills: ALWAYS `S.agronomBadge` (green); plus the
     verdict pill — `verdict == 'confirmed'` → `S.agronomConfirmed` (green),
     `'adjusted'` → `S.agronomAdjusted` (amber), `''` → no verdict pill.
  2. `review.expertSummary` (bodyLarge, w500, height 1.35) when non-empty.
  3. `review.expertNotes` as always-visible bullet rows (the `_BulletSection`
     row visual, no ExpansionTile) when non-empty.
  4. Preparations: `adjustedPreparations.isNotEmpty` →
     `PreparationsSection(preparations: review.adjustedPreparations,
     title: S.agronomAdjustedPreps)`; EMPTY →
     `Text(S.agronomKeepPreps, bodySmall, color: outline)` (the AI list
     stands — say so explicitly).

**`lib/features/interview/transcript_provider.dart`:**

```dart
/// The finished agronom (expert) review, rendered as its own card —
/// separate from the AI DiagnosisCardBubble (spec §7.2).
class AgronomCardBubble extends TranscriptBubble {
  const AgronomCardBubble(super.id, {required this.review});
  final AgronomReview review;
}

void addAgronomReview(AgronomReview review) {
  _finalizeFarmer();
  _finalizeAgent();
  _append(AgronomCardBubble(_id(), review: review));
}
```

`addHistory` gains ONE rule before the role switch: `kind ==
'agronom_review'` → `continue` (skip — the AgronomCard carries the content;
an OLD app without this rule shows it as a system bubble, which is the
intended degradation).

**`lib/features/interview/interview_screen.dart`:**

* `_BubbleView` becomes a `ConsumerWidget`. Its `DiagnosisCardBubble` case:

```dart
case DiagnosisCardBubble(:final result, :final preparations):
  final active = ref.watch(activeChatProvider);
  final chatBound = active != null && active.summary.id.isNotEmpty;
  final status =
      !chatBound ? '' : (ref.watch(agronomReviewProvider)?.status ?? 'none');
  return DiagnosisCard(
    result: result,
    preparations: preparations,
    agronomStatus: status,
    onAgronomRequest: status != 'none' ? null : () async {
      final ok = await ref
          .read(agronomReviewProvider.notifier)
          .request(active!.summary.id);
      if (!ok && context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text(S.agronomRequestFailed)));
      }
    },
  );
case AgronomCardBubble(:final review):
  return AgronomCard(review: review);
```

  (On success the provider flips to `pending` and the watch re-renders the
  card's pending row — no other feedback. On failure NOTHING changes except
  the snackbar: fail-open, the button stays.)

**`lib/features/session/voice_session_controller.dart`** — poll-on-open
wiring, exactly two touches:

* `start()`, right after the existing `addHistory` block: when the session
  is chat-bound, seed the provider and surface the stored review state once:

```dart
if (active != null && active.summary.id.isNotEmpty) {
  final review = active.summary.agronomReview;
  ref.read(agronomReviewProvider.notifier).set(review);
  final transcript = ref.read(transcriptProvider.notifier);
  if (review.status == 'pending') transcript.addSystem(S.agronomPending);
  if (review.status == 'done') transcript.addAgronomReview(review);
}
```

* `stop()`: `ref.read(agronomReviewProvider.notifier).clear();` next to the
  existing `activeChatProvider` close. (`stop()` already invalidates
  `chatListProvider` — that IS the list-side poll.)

**`lib/features/chat/chat_list_screen.dart`** — the list-side surfacing:
when `summary.agronomReview.status == 'done'`, prepend
`Icon(Icons.verified_user, size: 16, color: Colors.green)` + 4px gap before
the tile's title text. `pending`/`none` → no marker. (The summaries come
from the normal `GET /chats` fetch — no polling timers anywhere; NO FCM.)

**Polling model (frozen):** status is read ONLY (a) when the chat list
loads (`chatListProvider`), (b) when a chat is opened (`getChat` →
`ActiveChat.summary`), (c) from the `agronom-request` response. No timers,
no background refresh. A mock review typically lands seconds AFTER the call
ends (it starts at teardown), so the farmer usually sees `pending` when the
list refreshes at hang-up and `done` on the next open — that is the
accepted §7.2 stub UX.

### P3.8 Uzbek string table (Latin, `ʻ` U+02BB okina)

Mirrored keys — backend `chat/models.py` `UZ` ↔ mobile
`lib/features/chat/strings.dart` `S` (byte-exact, same rule as §6):

| key | Uzbek string | used for |
|-----|--------------|----------|
| `agronomSend` | `Agronomga yuborish` | diagnosis-card button (§7.1) |
| `agronomPending` | `Agronom tekshirmoqda…` | pending row + reopened-chat system bubble |
| `agronomCardTitle` | `Agronom (ekspert) javobi` | expert card header |
| `agronomBadge` | `Agronom tasdiqlagan javob` | expert card badge (§7.2) |
| `agronomMockLabel` | `AI yordamchi (sinov)` | is_mock pill — never mistakable for a human |
| `agronomConfirmed` | `Tashxis tasdiqlandi` | verdict pill, `confirmed` |
| `agronomAdjusted` | `Tavsiya aniqlashtirildi` | verdict pill, `adjusted` |
| `agronomKeepPreps` | `AI tavsiya etgan preparatlar roʻyxati oʻz kuchida qoladi.` | expert card, empty `adjusted_preparations` |
| `agronomAdjustedPreps` | `Yangilangan preparatlar roʻyxati` | expert card preparations title |
| `agronomRequestFailed` | `Yuborib boʻlmadi. Qayta urinib koʻring.` | request-failure snackbar |

Backend-ONLY strings (in `UZ` / module constants; NOT in `strings.dart`,
same rule as `prepPrefix`/P2.4):

| key / constant | Uzbek string | used for |
|-----|--------------|----------|
| `agronomPrefix` | `Agronom javobi:` | stored `agronom_review` chat message text |
| `_AGRONOM_OFFER_SPOKEN` | (P3.6 exact bytes) | the spoken §7 offer suffix |

The ellipsis in `agronomPending` is the single char `…` (U+2026). Do NOT
substitute plain `'` for `ʻ` anywhere.

### P3.9 Config — `backend/app/config.py`

```python
# ---- Agronom verification stub (docs/multichat_contract.md Phase 3) ----
# Master switch: the offer sentence, both /chats/{id}/agronom-* endpoints
# and the mock kickoffs. OFF = byte-identical Phase 2 behaviour.
agronom_enabled: bool = False
# AUTO "senior agronom" AI second opinion on each request (is_mock=true).
# A demo stub — flip OFF when real agronoms take over the submit endpoint.
agronom_mock_enabled: bool = False
# Shared secret for POST /chats/{id}/agronom-review (X-Agronom-Token
# header). Empty (default) = the human submit endpoint is disabled (404).
agronom_review_token: str = ""
```

Env names: `AGRONOM_ENABLED`, `AGRONOM_MOCK_ENABLED`,
`AGRONOM_REVIEW_TOKEN`. Deployment note: enable `agronom_enabled` only once
the Phase-3 app build is rolled out (P3.11 version-skew row).

### P3.10 File-by-file change list (Phase 3)

#### BACKEND FILES

| file | change |
|------|--------|
| `backend/app/config.py` | 3 new settings (P3.9). |
| `backend/app/voice/chat/models.py` | `AgronomReview` model + caps + `sanitize_expert_payload` (P3.1); `ChatDoc.agronom_review`; `build_summary`/`build_detail` emit `agronom_review`; `UZ` + the 10 mirrored keys + `agronomPrefix`; widen `ChatMessage` role/kind doc comments (`agronom`, `agronom_review`). |
| `backend/app/voice/chat/store.py` | `save()` agronom_review preservation rule (P3.1 — a doc that never saw a review can never erase one). |
| `backend/app/voice/chat/__init__.py` | Export `AgronomReview`, `sanitize_expert_payload`. |
| `backend/app/voice/agronom/__init__.py` | NEW (package marker). |
| `backend/app/voice/agronom/review.py` | NEW — `ExpertReview`, `AGRONOM_REVIEW_SYSTEM_PROMPT` + local candidate block, `review_diagnosis`, `_review_input`, `maybe_start_mock_review`, `_run_mock_review`, `REVIEW_TIMEOUT_S` (P3.5). |
| `backend/app/main.py` | `_AgronomRequestBody`/`_AgronomReviewBody` (module scope); `POST /chats/{chat_id}/agronom-request` (P3.3); `POST /chats/{chat_id}/agronom-review` with `X-Agronom-Token` + `compare_digest` (P3.4); both under `lock_for(chat_id)`. |
| `backend/app/voice/providers/gemini_live.py` | `__init__`: `self.agronom_offer = False`; `_AGRONOM_OFFER_SPOKEN`; `_diagnosis_spoken(..., *, offer_agronom=False)` suffix (P3.6); `_run_finalize_case`: pass `offer_agronom`, extend `last_diagnosis` with `result`/`summary`/`preparations_full` (P3.2). |
| `backend/app/voice/pipeline/voice_agent.py` | Chat-setup try: `session.agronom_offer = bool(settings.agronom_enabled)` (P3.6). Teardown: NEW separate try-block after chat finalize — fresh read + `maybe_start_mock_review` (P3.5). |
| `backend/app/voice/pipeline/diagnosis.py`, `backend/app/voice/enrich/treatments.py`, `backend/app/voice/chat/guide.py`, `backend/app/schemas.py` | NO changes (`get_crop_diseases`/`find_preparations_by_id` reused as-is; no WS schema change). |
| `backend/app/voice/tests/test_chat_models.py` | EXTEND — `AgronomReview` defaults; `build_summary`/`build_detail` carry `agronom_review` (None → null); `sanitize_expert_payload` caps/coercion/drop rules. |
| `backend/app/voice/tests/test_chat_store_agronom.py` | NEW — save() preservation: a review-less doc save keeps the on-disk pending/done review; a doc WITH a review writes it through; merge failure falls back to plain write. |
| `backend/app/voice/tests/test_agronom_endpoints.py` | NEW — request: disabled→404, bad user→400, wrong owner→404, first request sets pending+requested_at, idempotent repeat, done unchanged; review: disabled/empty-token→404, bad token→401, not-requested→409, bad verdict→400, empty summary→400, pending→done (is_mock false), done-overwrites-mock, sanitizer applied, message appended with `Agronom javobi:` prefix. |
| `backend/app/voice/tests/test_agronom_review.py` | NEW — fake genai client (mirror `test_diagnosis.py`): `_review_input` full vs thin `last_diagnosis`; runner happy path writes done+is_mock; keep_preparations→`[]`; adjusted id → `find_preparations_by_id` result; genai error/timeout → stays pending, no raise; human-wins race (status done under lock → dropped); `maybe_start_mock_review` flag/status/diagnosis gating. |
| `backend/app/voice/tests` (existing gemini_live/diagnosis-spoken tests) | EXTEND — `_diagnosis_spoken` byte-identity when `offer_agronom=False` (default); suffix appended when True (with and without preparations); `last_diagnosis` carries the 3 new keys. |

Infra: NO changes (nginx `^~ /chats` already covers `/chats/{id}/agronom-*`;
same data volume).

#### MOBILE FILES (all under `mobile/`)

| file | change |
|------|--------|
| `lib/features/chat/chat.dart` | `AgronomReview` model (+`none()`/`fromJson`); `ChatSummary.agronomReview`; import `events.dart` for `Preparation` (P3.7). |
| `lib/features/chat/chat_service.dart` | `requestAgronomReview(chatId, userId)` (P3.7). |
| `lib/features/chat/chat_providers.dart` | `AgronomReviewController` + `agronomReviewProvider` (P3.7). |
| `lib/features/chat/strings.dart` | The 10 `agronom*` keys (P3.8, exact bytes). |
| `lib/features/chat/chat_list_screen.dart` | `verified_user` green 16px icon before the title when `agronomReview.status == 'done'`. |
| `lib/features/diagnosis/diagnosis_card.dart` | `agronomStatus`/`onAgronomRequest` params + button/pending row after preparations; rename `_PreparationsSection` → public `PreparationsSection` with a `title` param (P3.7). |
| `lib/features/diagnosis/agronom_card.dart` | NEW — the visually-distinct expert card (border+tertiary header, badge, verdict pill, mock label, notes, adjusted/keep preparations) (P3.7). |
| `lib/features/interview/transcript_provider.dart` | `AgronomCardBubble` + `addAgronomReview`; `addHistory` skips `kind == 'agronom_review'` (P3.7). |
| `lib/features/interview/interview_screen.dart` | `_BubbleView` → `ConsumerWidget`; DiagnosisCard agronom wiring + failure snackbar; `AgronomCardBubble` case (P3.7). |
| `lib/features/session/voice_session_controller.dart` | `start()`: seed `agronomReviewProvider` + pending bubble / done card for chat-bound opens; `stop()`: clear the provider (P3.7). |
| `lib/core/protocol/events.dart` | NO changes (no new WS events in Phase 3). |
| `test/agronom_models_test.dart` | NEW — `AgronomReview.fromJson`: null → none; full object; string-typed doses in `adjusted_preparations` coerce; unknown status string passes through. |
| camera/avatar/guide files | NO changes. |

### P3.11 FAIL-OPEN law (Phase 3 — on top of ALL v1/v2/P2 rules)

A review failure must NEVER touch the diagnosis or the call.

* **Master flag off** (`agronom_enabled=False`, the default): both endpoints
  404, no voice offer (`agronom_offer` stays False), no mock kickoffs, no
  behaviour change anywhere — byte-identical to Phase 2. The three
  `last_diagnosis` keys and the `agronom_review: null` summary key are the
  only (inert, additive) observable deltas.
* **The diagnosis path is untouchable**: inside `_run_finalize_case` Phase 3
  adds ONLY three dict keys and a pure-string spoken suffix — no new awaits,
  no new failure modes before the `case.diagnosis` event. The mock review
  runs in its own task AFTER the fact, off the chat file, never off the live
  session.
* **Mock review fails → `pending` forever, silently**: any genai error,
  timeout (60s), MAX_TOKENS, unparsable JSON, empty summary, or store race →
  one WARNING log, status stays `pending`, the farmer sees «Agronom
  tekshirmoqda…» and a human can still complete the review. NEVER an error
  event, NEVER a crash.
* **Kickoff seams are wrapped**: both `maybe_start_mock_review` call sites
  (endpoint + teardown) sit in their own try/except; the teardown one runs
  AFTER the chat-finalize try, so it can never break diagnosis persistence
  or memory finalize.
* **Request button fails open**: any non-200 (including 404 when the flag is
  off) → `false` → one snackbar (`agronomRequestFailed`); nothing else
  changes and the button remains.
* **Store clobber protection**: the `save()` preservation rule (P3.1) means
  mid-call guide/teardown saves cannot erase a concurrently-created pending
  review; if the merge itself fails, the worst case is the pre-P3 behaviour
  (review lost, chat intact).
* **Human always wins**: mock-vs-human races resolve under `lock_for`
  (P3.5); a human submit overwrites any mock; a late mock drops itself.
* **Version skew**: P3 backend + P2 app — `agronom_review` key and
  `agronom` messages degrade gracefully (ignored key / system bubble), but
  Rais would offer a button the app doesn't have, so keep `agronom_enabled`
  OFF until the P3 app ships (P3.9 note). P2 backend + P3 app — the button
  shows on chat-bound diagnoses, the request 404s → snackbar, everything
  else works; ship the backend first. Neither skew breaks a call or a chat.

END OF PHASE 3 ADDENDUM. The `AgronomReview` shape and status values, both
endpoint paths/bodies/status codes, the `X-Agronom-Token` header, the
`ExpertReview` schema and prompt, the caps (600/300/6/4, 60s timeout), the
kickoff seams, the store preservation rule, the spoken offer suffix and
every Uzbek string above are FROZEN; anything not specified is the
implementer's choice as long as observable behaviour matches this document.

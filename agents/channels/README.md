# Channel adapters — adding a new one

CM-29 ships the framework: `NormalizedMessage` + the `ChannelAdapter`
Protocol + a reference `WebAdapter`. WhatsApp, Telegram, Email, voice
transcription, and image OCR each get their own story (CM-31..CM-35).
This file is the recipe for picking up one of those stories.

## The shape

```
agents/channels/
├── schema.py            ← NormalizedMessage + Channel + Attachment union
├── base.py              ← ChannelAdapter Protocol + NormalizationError
├── web.py               ← reference adapter (copy this!)
├── whatsapp.py          ← CM-31 — Twilio webhook + media refetch
├── telegram.py          ← CM-32 — Bot API webhook + entities
├── email.py             ← CM-33 — IMAP poll + RFC-822 parse
└── preprocessors/
    ├── audio.py         ← AudioTranscriber + NoopAudioTranscriber (CM-29)
    │                       Real Azure AI Speech: CM-34
    └── image.py         ← ImageOcr + NoopImageOcr (CM-29)
                            Real Azure AI Vision: CM-35
```

## Recipe — new adapter (CM-31 / CM-32 / CM-33)

1. **Add an enum value to `Channel`** in `schema.py` if it isn't there
   already (it is — WhatsApp / Telegram / Email all enumerated by
   CM-29).
2. **Create `agents/channels/<channel>.py`**. Copy `web.py` as the
   starting shape — it's intentionally minimal so the divergence is
   visible. Replace `Channel.WEB` with your channel.
3. **Implement `async def normalize(self, raw)`**. The vendor payload
   for your channel is fixed — document the shape in the class
   docstring, mirror what `WebAdapter` does. If you need to re-fetch
   media (Twilio MediaUrl, Telegram getFile), this is where the async
   shines.
4. **Wrap any underlying error in `NormalizationError`**. Never let
   `pydantic.ValidationError` propagate to the HTTP handler — it leaks
   schema internals to a potentially-malicious client.
5. **PII-mask the content BEFORE constructing the NormalizedMessage**.
   Call `agents.observability.mask_pii(text)` (CM-27) and feed the
   masked string into `NormalizedMessage.content`. The schema does not
   re-mask.
6. **Re-export from `agents/channels/__init__.py`** so callers can
   `from agents.channels import WhatsAppAdapter` etc.
7. **Write `tests/channels/test_<channel>.py`** modeled on
   `test_web.py`: happy path + missing-field + extra-field + adapter
   recognized as `ChannelAdapter` via `isinstance`. Bonus: vendor-shape
   fixture validating the documented payload structure.
8. **No new KV secret needed** unless the channel has one (Twilio auth
   token for WhatsApp, etc.). Add it to `keyvault.bicep:secretNames`
   and `seed-keyvault-secrets.sh:SECRETS` if so.

## Recipe — replace a preprocessor stub (CM-34 / CM-35)

1. Implement a class satisfying the relevant Protocol (`AudioTranscriber`
   or `ImageOcr`). Put it next to the stub:
   `agents/channels/preprocessors/azure_speech.py` (CM-34)
   `agents/channels/preprocessors/azure_vision.py` (CM-35)
2. The class constructor takes the vendor SDK client (or factory).
   Real Azure SDK calls happen in the implementation method, NOT at
   construction — DI patterns work this way.
3. Add the KV secret name to `keyvault.bicep:secretNames` (e.g.
   `azure-ai-speech-key`) and update `seed-keyvault-secrets.sh`.
4. Wire selection at app boot: read env (`USE_REAL_TRANSCRIBER=true`
   or similar) and pick `NoopAudioTranscriber` vs your real class.
5. **Latency budget**: each preprocessor has its own. CM-29's
   `<500ms median` AC measures the normalization fast path (Pydantic +
   adapter logic). When you wire a real vendor call, document its
   median round-trip in `docs/INFRA.md` and add a separate budget test.
6. Tests in `tests/channels/test_<preprocessor>.py` should mock the
   vendor SDK (`respx` for HTTP-based ones, or an injected fake) so the
   suite stays hermetic.

## Conventions

* **Async everywhere.** Even sync adapters declare `async def normalize`
  for Protocol uniformity. Future vendor adapters need async.
* **No mutable state on adapter instances.** Adapters are reused
  across requests; instance attributes must be immutable
  configuration (vendor clients, base URLs, … never per-request state).
* **One `Channel` enum value per channel.** No "WhatsApp Business" vs
  "WhatsApp Personal" split at the enum layer — that distinction
  belongs in vendor-specific metadata if it matters.
* **Frozen `NormalizedMessage`.** Downstream agents must not mutate.
  Build new `AgentState` instances with updated fields instead — that's
  the LangGraph idiom and CM-28's `AgentState.merge(updates)` is the
  helper.

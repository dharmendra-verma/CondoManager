"""Voice → text Protocol + no-op stub.

Jira: CM-29  | Epic: CM-Epic 3 (Channel Adapters)  | Phase 0

The ``NoopAudioTranscriber.transcribe()`` return string is part of the
public contract — the downstream Triage agent (CM-30) recognises this
exact sentinel and surfaces "audio attached but no transcript
available" politely to the operator. Real Azure AI Speech wiring lands
in CM-34, swapping `NoopAudioTranscriber` for a vendor implementation
via DI at app boot.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

#: Sentinel returned by ``NoopAudioTranscriber.transcribe``. Triage
#: recognises this exact string and surfaces a polite "audio attached"
#: message to the operator. Do not change without coordinating with
#: CM-30 (Triage) prompts.
NOOP_AUDIO_SENTINEL: str = "[audio: not transcribed]"


@runtime_checkable
class AudioTranscriber(Protocol):
    """Voice → text. Real implementation lands in CM-34 (Azure AI Speech).

    Implementations must be stateless and safe to call concurrently.
    """

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str = "en-US",
    ) -> str:
        """Return the transcript text for the given audio blob.

        Args:
            audio_bytes: Raw audio in the channel's native format.
                Implementations are responsible for any container or
                codec handling.
            language: BCP-47 tag for the expected language. Defaults to
                ``en-US`` but adapters MAY pass through a channel-supplied
                hint (Twilio's ``MediaUrl0`` headers, Telegram language
                code) if available.

        Returns:
            The transcript as a single string. Long-form transcripts may
            include line breaks; callers must not assume a single line.
        """
        ...


class NoopAudioTranscriber:
    """Default ``AudioTranscriber`` until CM-34 wires Azure AI Speech.

    Returns the documented sentinel so Triage can recognize the no-op
    case explicitly. NOT a placeholder ``...`` body — the sentinel is the
    public contract.
    """

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str = "en-US",
    ) -> str:
        return NOOP_AUDIO_SENTINEL

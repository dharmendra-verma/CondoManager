"""Image OCR Protocol + no-op stub.

Jira: CM-29  | Epic: CM-Epic 3 (Channel Adapters)  | Phase 0

Same pattern as ``audio.py`` — Protocol + stub returning a documented
sentinel string that Triage (CM-30) handles gracefully. Real Azure AI
Vision wiring lands in CM-35.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

#: Sentinel returned by ``NoopImageOcr.extract_text``. Triage recognises
#: this exact string and surfaces "image attached, OCR unavailable" to
#: the operator. Do not change without coordinating with CM-30 prompts.
NOOP_IMAGE_SENTINEL: str = "[image: OCR not configured]"


@runtime_checkable
class ImageOcr(Protocol):
    """Image → extracted text. Real implementation lands in CM-35.

    Implementations must be stateless and safe to call concurrently.
    """

    async def extract_text(
        self,
        image_bytes: bytes,
        *,
        mime_type: str | None = None,
    ) -> str:
        """Extract textual content from the given image.

        Args:
            image_bytes: Raw image in the source format (JPEG / PNG /
                HEIC / etc.). Implementations are responsible for
                detecting the format if ``mime_type`` is None.
            mime_type: Optional hint from the channel adapter (e.g.
                Twilio's ``MediaContentType``). When supplied,
                implementations may skip a format-detection round-trip.

        Returns:
            The extracted text as a single string. Layout (line breaks,
            paragraph separation) follows the underlying OCR engine's
            output; callers should not assume normalized whitespace.
        """
        ...


class NoopImageOcr:
    """Default ``ImageOcr`` until CM-35 wires Azure AI Vision.

    Returns the documented sentinel so Triage can recognize the no-op
    case explicitly.
    """

    async def extract_text(
        self,
        image_bytes: bytes,
        *,
        mime_type: str | None = None,
    ) -> str:
        return NOOP_IMAGE_SENTINEL

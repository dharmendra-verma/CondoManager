"""Tests for the CM-23 LangSmith helpers.

We mock ``langsmith.Client`` so the tests stay offline. The assertions cover:

* :func:`is_langsmith_enabled` truth table — all 6 corners of (TRACING_V2 in
  {unset, 'false', 'true'}) x (API_KEY in {unset, REPLACE-ME, real}).
* :func:`ensure_dataset` creates the dataset when it doesn't exist, reuses
  it when it does, and adds only the examples whose ``inputs`` fingerprint
  isn't already present (idempotency).
* The CM-23 dual-emission posture: setting LangSmith env vars alongside
  the App Insights conn string doesn't disturb the CM-22 Azure Monitor
  branch of ``setup_tracer_provider`` (regression).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from agents.observability import langsmith, sdk

# ---------------------------------------------------------------------------
# is_langsmith_enabled — truth table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tracing_v2", "api_key", "expected"),
    [
        # No tracing flag at all -> False, regardless of key.
        (None, None, False),
        (None, "real-key", False),
        # Flag present but not "true".
        ("false", "real-key", False),
        ("0", "real-key", False),
        # The SDK is case-INsensitive on the flag — "True" / "TRUE" both work.
        ("True", "real-key", True),
        ("TRUE", "real-key", True),
        # Flag is "true" but the key is missing / placeholder / blank.
        ("true", None, False),
        ("true", "", False),
        ("true", "   ", False),
        ("true", "REPLACE-ME", False),
        # The one happy path: flag = true AND key is a real value.
        ("true", "real-key", True),
    ],
)
def test_is_langsmith_enabled_truth_table(
    monkeypatch: pytest.MonkeyPatch,
    tracing_v2: str | None,
    api_key: str | None,
    expected: bool,
) -> None:
    if tracing_v2 is None:
        monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    else:
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", tracing_v2)
    if api_key is None:
        monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    else:
        monkeypatch.setenv("LANGCHAIN_API_KEY", api_key)

    assert langsmith.is_langsmith_enabled() is expected


# ---------------------------------------------------------------------------
# ensure_dataset — idempotency + dedup
# ---------------------------------------------------------------------------


def _mock_client(*, existing_inputs: list[dict[str, Any]] | None = None) -> MagicMock:
    """Build a mocked ``langsmith.Client`` returning the given existing examples."""
    client = MagicMock()
    if existing_inputs is None:
        # `read_dataset` raises -> ensure_dataset will create.
        client.read_dataset.side_effect = Exception("not found")
        dataset = SimpleNamespace(id="ds-new")
        client.create_dataset.return_value = dataset
        client.list_examples.return_value = []
    else:
        dataset = SimpleNamespace(id="ds-existing")
        client.read_dataset.return_value = dataset
        client.list_examples.return_value = [
            SimpleNamespace(inputs=inputs) for inputs in existing_inputs
        ]
    client._dataset = dataset  # for assertions
    return client


def test_ensure_dataset_creates_when_absent() -> None:
    examples = [
        {"inputs": {"message": "a"}, "outputs": {"intent": "x"}},
        {"inputs": {"message": "b"}, "outputs": {"intent": "y"}},
    ]
    mock = _mock_client(existing_inputs=None)
    with patch.object(langsmith, "client", return_value=mock):
        ds = langsmith.ensure_dataset("test-ds", examples, description="x")

    assert ds is mock._dataset
    mock.create_dataset.assert_called_once_with(
        dataset_name="test-ds", description="x"
    )
    # Both examples uploaded.
    assert mock.create_example.call_count == 2


def test_ensure_dataset_skips_existing_dataset() -> None:
    examples = [{"inputs": {"message": "a"}, "outputs": {"intent": "x"}}]
    mock = _mock_client(existing_inputs=[])
    with patch.object(langsmith, "client", return_value=mock):
        langsmith.ensure_dataset("test-ds", examples)

    # The dataset already exists -> no create_dataset call.
    mock.create_dataset.assert_not_called()
    mock.create_example.assert_called_once()


def test_ensure_dataset_dedupes_already_present_examples() -> None:
    """Re-uploading the same JSONL must not append duplicate examples."""
    examples = [
        {"inputs": {"message": "a"}, "outputs": {"intent": "x"}},
        {"inputs": {"message": "b"}, "outputs": {"intent": "y"}},
        {"inputs": {"message": "c"}, "outputs": {"intent": "z"}},
    ]
    # Two of three already exist.
    mock = _mock_client(existing_inputs=[{"message": "a"}, {"message": "b"}])

    with patch.object(langsmith, "client", return_value=mock):
        langsmith.ensure_dataset("test-ds", examples)

    # Only the third example should land.
    assert mock.create_example.call_count == 1
    _, kwargs = mock.create_example.call_args
    assert kwargs["inputs"] == {"message": "c"}


def test_ensure_dataset_uses_stable_fingerprint_for_dedup() -> None:
    """Key-order in `inputs` shouldn't affect dedup."""
    examples = [
        {"inputs": {"b": 2, "a": 1}, "outputs": {}},
    ]
    # Existing example has the same keys but in different order.
    mock = _mock_client(existing_inputs=[{"a": 1, "b": 2}])
    with patch.object(langsmith, "client", return_value=mock):
        langsmith.ensure_dataset("test-ds", examples)
    mock.create_example.assert_not_called()


# ---------------------------------------------------------------------------
# CM-22 / CM-23 dual-emission regression
# ---------------------------------------------------------------------------

FAKE_APPI_CONN = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
    "IngestionEndpoint=https://eastus2.in.applicationinsights.azure.com/;"
)


def test_appi_branch_wins_when_langsmith_env_also_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LangSmith env vars don't disturb the CM-22 Azure Monitor branch.

    The two pipelines hook different layers: Azure Monitor is the OTel
    exporter for ``setup_tracer_provider``; LangSmith is a LangChain native
    callback that doesn't touch the OTel tracer. Setting LangSmith env
    vars therefore must NOT push `setup_tracer_provider` off its Azure
    Monitor path.
    """
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", FAKE_APPI_CONN)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "ls-test-key")
    monkeypatch.setenv("LANGCHAIN_PROJECT", "condomanager-dev")

    with patch("azure.monitor.opentelemetry.configure_azure_monitor") as mock_distro:
        sdk.setup_tracer_provider(service_name="svc", environment="dev")
    mock_distro.assert_called_once()

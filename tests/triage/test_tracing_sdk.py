"""Shared fake Langfuse SDK for C10 tracing tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeTextPromptClient:
    prompt: str
    version: int = 1
    name: str = "triage-system"
    is_fallback: bool = False


@dataclass
class RecordedObservation:
    name: str
    as_type: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    updates: list[dict[str, Any]] = field(default_factory=list)

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


class FakeLangfuseSDK:
    """Records Langfuse client calls without network."""

    def __init__(
        self,
        *,
        public_key: str = "",
        secret_key: str = "",
        base_url: str = "",
        timeout: int = 5,
        fail_after_init: bool = False,
    ) -> None:
        self.init_kwargs = {
            "public_key": public_key,
            "secret_key": secret_key,
            "base_url": base_url,
            "timeout": timeout,
        }
        self.observations: list[RecordedObservation] = []
        self.propagates: list[dict[str, Any]] = []
        self.scores: list[dict[str, Any]] = []
        self.shutdown_calls = 0
        self._fail = fail_after_init
        self._stack: list[RecordedObservation] = []
        self.get_prompt_calls: list[dict[str, Any]] = []
        self.create_prompt_calls: list[dict[str, Any]] = []
        self.prompt_client: FakeTextPromptClient | None = None
        self.get_prompt_exc: BaseException | None = None

    def get_prompt(self, **kwargs: Any) -> FakeTextPromptClient:
        if self._fail:
            raise RuntimeError("sdk_down")
        self.get_prompt_calls.append(dict(kwargs))
        if self.get_prompt_exc is not None:
            raise self.get_prompt_exc
        if self.prompt_client is None:
            raise RuntimeError("no prompt")
        return self.prompt_client

    def create_prompt(self, **kwargs: Any) -> FakeTextPromptClient:
        if self._fail:
            raise RuntimeError("sdk_down")
        self.create_prompt_calls.append(dict(kwargs))
        version = len(self.create_prompt_calls)
        return FakeTextPromptClient(
            prompt=str(kwargs.get("prompt", "")),
            version=version,
            is_fallback=False,
        )

    def start_as_current_observation(self, **kwargs: Any) -> Any:
        if self._fail:
            raise RuntimeError("sdk_down")
        obs = RecordedObservation(
            name=str(kwargs.get("name", "")),
            as_type=str(kwargs.get("as_type", "")),
            kwargs=dict(kwargs),
        )

        @contextmanager
        def _cm() -> Any:
            self.observations.append(obs)
            self._stack.append(obs)
            try:
                yield obs
            finally:
                if self._stack and self._stack[-1] is obs:
                    self._stack.pop()

        return _cm()

    def score_current_trace(self, **kwargs: Any) -> None:
        if self._fail:
            raise RuntimeError("sdk_down")
        self.scores.append(dict(kwargs))

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        if self._fail:
            raise RuntimeError("sdk_down")


def make_propagate_attributes(sdk: FakeLangfuseSDK) -> Callable[..., Any]:
    """Module-level stand-in for ``langfuse.propagate_attributes`` (per fake client)."""

    @contextmanager
    def propagate_attributes(**kwargs: Any) -> Iterator[FakeLangfuseSDK]:
        if sdk._fail:
            raise RuntimeError("sdk_down")
        sdk.propagates.append(dict(kwargs))
        yield sdk

    return propagate_attributes
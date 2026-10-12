"""Fixed observations for software tests. This provider does not inspect images."""

from pathlib import Path

from ..contracts import AuditInputV1
from ..errors import ProviderError
from ..ports import ModelResponse


class FakeProvider:
    name = "fake-fixed-v1"
    live = False

    def __init__(self, response: str) -> None:
        self.response = response

    @classmethod
    def from_file(cls, path: str | Path) -> "FakeProvider":
        try:
            return cls(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise ProviderError(f"cannot read fixed response: {exc}") from exc

    async def invoke(self, audit: AuditInputV1, prompt: str) -> ModelResponse:
        return ModelResponse(text=self.response)

"""Load the shared prompt and default message-led variant from the original file."""

from pathlib import Path
import re


def default_prompt_path() -> Path:
    source = Path(__file__).resolve().parents[2] / "examples" / "creative-director-samples" / "prompts.md"
    if source.is_file():
        return source
    # Wheel builds include the same original Markdown file.
    return Path(__file__).parent / "resources" / "prompts.md"


def _text_block(markdown: str, heading: str) -> str:
    level = len(heading.split(" ", 1)[0])
    section = re.search(
        rf"(?m)^{re.escape(heading)}[ \t]*\n([\s\S]*?)(?=^#{{1,{level}}} |\Z)",
        markdown,
    )
    if section is None:
        raise ValueError(f"Missing prompt section: {heading}")
    blocks = re.findall(r"(?ms)^```text[ \t]*\n(.*?)^```[ \t]*$", section[1])
    if len(blocks) != 1 or not blocks[0].strip():
        raise ValueError(f"Expected one non-empty text block under {heading}")
    return blocks[0].strip()


def load_prompt(*, prompt_path: Path | None = None) -> str:
    path = prompt_path if prompt_path is not None else default_prompt_path()
    markdown = path.read_text(encoding="utf-8")
    shared = _text_block(markdown, "## Shared initial prompt")
    message_led = _text_block(markdown, "### message-led")
    return shared + "\n\n" + message_led


def load_revision_prompt(*, prompt_path: Path | None = None) -> str:
    """Read the extra instructions used only when revising an existing brief."""
    path = prompt_path if prompt_path is not None else default_prompt_path()
    return _text_block(path.read_text(encoding="utf-8"), "## Revision instructions")

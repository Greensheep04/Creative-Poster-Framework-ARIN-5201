import importlib
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_PATH = PROJECT_ROOT / "examples" / "creative-director-samples" / "prompts.md"


@pytest.fixture(scope="module")
def director():
    return importlib.import_module("creative.creative_director")


def text_under_heading(document, heading):
    """Independent extraction for this known fixture, not the production parser."""
    section = document.split(heading + "\n", 1)[1]
    return section.split("```text\n", 1)[1].split("\n```", 1)[0].strip("\r\n")


def small_document(shared="Shared instruction.", message="Make one clear poster."):
    return (
        "# Creative Director prompts\n\n"
        f"## Shared initial prompt\n\n```text\n{shared}\n```\n\n"
        "## Prompt variants\n\n"
        f"### message-led\n\n```text\n{message}\n```\n\n"
        "### visual-metaphor\n\n```text\nDo not include this other variant.\n```\n\n"
        "## Keyword inputs\n\n| Folder | Input |\n| example | keywords |\n\n"
        "## Output files\n\n```text\nNever include this output listing.\n```\n"
    )


def test_default_prompt_is_exact_shared_block_and_message_led(director):
    document = PROMPTS_PATH.read_text(encoding="utf-8")
    expected = (
        text_under_heading(document, "## Shared initial prompt")
        + "\n\n"
        + text_under_heading(document, "### message-led")
    )

    assert director.load_prompt() == expected
    assert director.load_prompt(prompt_path=PROMPTS_PATH) == expected


def test_revision_instructions_are_loaded_separately_from_original_file(director):
    document = PROMPTS_PATH.read_text(encoding="utf-8")
    revision = director.load_revision_prompt()

    assert revision == text_under_heading(document, "## Revision instructions")
    assert revision not in director.load_prompt()


def test_missing_revision_section_does_not_affect_initial_generation(director, tmp_path):
    path = tmp_path / "prompts.md"
    path.write_text(small_document(), encoding="utf-8")

    assert director.load_prompt(prompt_path=path) == "Shared instruction.\n\nMake one clear poster."
    with pytest.raises(ValueError, match="Revision instructions"):
        director.load_revision_prompt(prompt_path=path)


def test_edits_to_prompt_source_are_reflected_on_next_load(director, tmp_path):
    path = tmp_path / "prompts.md"
    path.write_text(small_document(), encoding="utf-8")
    assert director.load_prompt(prompt_path=path) == "Shared instruction.\n\nMake one clear poster."

    path.write_text(
        small_document("Updated shared instruction.", "Updated default instruction."),
        encoding="utf-8",
    )

    assert director.load_prompt(prompt_path=path) == (
        "Updated shared instruction.\n\nUpdated default instruction."
    )


def test_other_variants_and_document_sections_are_ignored(director, tmp_path):
    shared = "First line.\n  Preserve this indentation.\nLast line."
    path = tmp_path / "prompts.md"
    document = small_document(shared).replace(
        "```text\nDo not include this other variant.\n```", "An unfinished unrelated variant."
    )
    path.write_text(document, encoding="utf-8")

    assert director.load_prompt(prompt_path=path) == shared + "\n\nMake one clear poster."


@pytest.mark.parametrize(
    "document",
    [
        small_document().replace("## Shared initial prompt", "## Different section"),
        small_document().replace("### message-led", "### Different section"),
        small_document(shared=""),
        small_document(message=""),
        small_document().replace("Shared instruction.\n```", "Shared instruction."),
        small_document().replace("Make one clear poster.\n```", "Make one clear poster."),
    ],
    ids=[
        "missing-shared-section",
        "missing-message-led-section",
        "empty-shared-block",
        "empty-message-led-block",
        "unclosed-shared-block",
        "unclosed-message-led-block",
    ],
)
def test_invalid_required_prompt_blocks_are_rejected(director, tmp_path, document):
    path = tmp_path / "prompts.md"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ValueError):
        director.load_prompt(prompt_path=path)


def test_cli_uses_default_prompt_without_variant_option():
    completed = subprocess.run(
        [sys.executable, "-m", "creative", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "--variant" not in completed.stdout

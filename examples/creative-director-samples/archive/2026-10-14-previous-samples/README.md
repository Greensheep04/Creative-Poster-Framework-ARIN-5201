# Creative Director sample outputs

Six manually collected model responses for two keyword sets. These are exploratory samples, not a benchmark or approved agent schema.

The [original sample document](sample-outputs-cd.md) is preserved unchanged and includes the shared prompt, case-specific constraints, model labels, and raw responses. The JSON files below preserve each response's parsed content; only whitespace and indentation have been normalized. No missing fields or inconsistent types have been repaired.

## CD inputs for the two samples

The CD request contains `keywords` (an array of strings) and `constraints` (an object), following the structure of the framework's [sample request](../sample-request.json). Below are JSON representations of the actual keywords and constraints recorded for these two cases. The original tests supplied them as prompt text; the constraint field names below document that content and are not a finalized team schema.

### Ocean, plastic, hope

```json
{
  "keywords": ["ocean", "plastic", "hope"],
  "constraints": {
    "tone": "optimistic",
    "headline_language": "English",
    "headline_max_words": 7,
    "supporting_copy_max_words": 18,
    "main_visual_metaphor_count": 1,
    "must_not_invent": ["statistics", "brands", "organizations"],
    "layout_instruction": "Leave exact positions and font sizes to the Layout Planner."
  }
}
```

### Tradition, technology, harmony

```json
{
  "keywords": ["tradition", "technology", "harmony"],
  "constraints": {
    "headline_language": "English",
    "headline_max_words": 6,
    "include_supporting_copy": false,
    "palette_color_count": 2,
    "allow_split_screen": false,
    "main_visual_metaphor_count": 1
  }
}
```

Both cases also used the shared instruction to create one coherent brief for a 720 × 1280 portrait poster, return only JSON with the requested brief fields, and include exactly one concrete visual or textual mapping per keyword. The complete shared prompt is preserved in the [original sample document](sample-outputs-cd.md). The files linked in the table below are model **outputs** for these inputs.

## Samples

| Keywords | Qwen3-4B-Instruct-2507 | Qwen3-1.7B | Qwen3.5-4B |
| --- | --- | --- | --- |
| ocean, plastic, hope | [JSON](ocean-plastic-hope/qwen3-4b-instruct-2507.json) | [JSON](ocean-plastic-hope/qwen3-1.7b.json) | [JSON](ocean-plastic-hope/qwen3.5-4b.json) |
| tradition, technology, harmony | [JSON](tradition-technology-harmony/qwen3-4b-instruct-2507.json) | [JSON](tradition-technology-harmony/qwen3-1.7b.json) | [JSON](tradition-technology-harmony/qwen3.5-4b.json) |

## Interpretation and validation

For new tests, use the [revised CD prompt](prompt-v2.md). It explicitly defines required fields and nested types, empty supporting copy, inclusion/exclusion instructions, and consistency of the central visual idea across text and imagery. The existing outputs below were collected with the original prompt, not this revision.

All six extracted files parse as JSON. Valid JSON alone does not establish semantic quality or compliance with a final schema.

- In the ocean/plastic/hope case, Qwen3.5-4B returns arrays for `style.mood` and `style.medium`, whereas the other five responses use strings. The prompt specifies an object with those fields but does not explicitly specify their types; the team should finalize these nested types before treating this difference as a schema failure.
- In the tradition/technology/harmony case, Qwen3-1.7B omits the requested `supporting_copy` field instead of returning an empty string. This omission is retained.
- Model identifiers are copied from the document. Provider, exact checkpoint revision, sampling settings, quantization, and generation dates were not recorded, so these samples should not be treated as a controlled or fully reproducible comparison.
- These files contain CD brief content, not the framework's complete `AgentOutput` wrapper and not fake poster-image candidates.

# Creative Director keyword-only samples

Collected from Hugging Face on 14 October 2026 (Asia/Hong_Kong). The CD expands keywords into a meaningful poster concept for the VP. The user supplies no additional constraints.

## Results

The active collection contains **31 usable JSON briefs**. All parse as JSON, match the schema, preserve the exact input keywords, and match captured API content. These checks do not assess creative quality.

| Prompt type | Qwen3-4B-Instruct-2507 | Qwen3-1.7B | Qwen3.5-4B |
| --- | --- | --- | --- |
| [Message-led](comparisons/message-led.md) | 5 valid | 5 valid | 1 valid |
| [Visual metaphor](comparisons/visual-metaphor.md) | 5 valid | 5 valid | 0 retained |
| [Human story](comparisons/human-story.md) | 5 valid | 5 valid | 0 retained |

All 45 original combinations were attempted. The 13 truncated Qwen3.5 responses and one provider failure are preserved in [the unusable-runs archive](archive/2026-10-14-unusable-runs/README.md), together with failed attempts and their evidence. They are excluded from `outputs/` and the comparison tables. Qwen3.5's successful message-led/home-migration-memory brief remains active.

The truncated responses report `finish_reason: length`. They contain lengthy reasoning, with twelve empty final answers and one incomplete JSON answer, consistent with exhausting the generation allowance before completing the final JSON. The widget did not expose the numeric limit or thinking-mode controls. The saved files preserve exactly what the provider returned.

## Read the samples

- [Message-led comparison](comparisons/message-led.md)
- [Visual-metaphor comparison](comparisons/visual-metaphor.md)
- [Human-story comparison](comparisons/human-story.md)
- [Validation report](validation-report.md)
- [Detailed collection status and fidelity audit](collection-status.json)

`outputs/<prompt-type>/<keyword-set>/<model>` groups each sample's files:

- `.request.txt`: exact text submitted in the Hugging Face widget.
- `.raw.txt`: exact final model content for a retained valid brief.
- `.json`: parsed content with indentation only, present when parsing succeeded.
- `.response.json` or `.api-response.json`: the complete captured API response displayed by the widget.
- `.reasoning.txt`: reasoning text, where separately captured; full provider data remains in the API response.
- `.metadata.json`: model, provider, source, capture time, finish status, and input adaptation.
- `.evidence.png`: selected browser evidence; additional evidence is under `evidence/`.

## Prompts and inputs

[Shared instructions](shared-instructions.txt), [three full prompts](prompts/), [five keyword sets](keywords.json), [15 intended input pairs](inputs/), and the [strict JSON schema](schema.json) are preserved. The full prompts retain the exact wording proposed in the conversation.

The prepared inputs use separate system/user roles. The Hugging Face model-page widget exposes a single-line user input, so each actual submission combined the two messages with a blank line and replaced every newline with one space. No prompt words were changed. This is documented separately from the intended input representation.

The shared sentence “All other values must be strings” is retained verbatim; the example defines the named containers as objects. The schema applies string types to their leaf fields.

## Collection method

Official model pages were used with the Featherless AI provider:

- https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507
- https://huggingface.co/Qwen/Qwen3-1.7B
- https://huggingface.co/Qwen/Qwen3.5-4B

Prompt types ran in order: message-led, visual-metaphor, then human-story. Both active models' five-case sets were saved before advancing to the next prompt type. Samples used fresh chats and were saved incrementally. [run-plan.json](run-plan.json) retains the original inventory and model status.

Sampling parameters, exact checkpoint revisions, and quantization were not exposed by the widget. These are exploratory outputs, not a controlled model benchmark. Recorded failed network calls, superseded whitespace attempts, and capture retries are preserved in the unusable-runs archive and excluded from completed sample counts.

## Previous collection

Earlier samples and supporting files are preserved under [archive/2026-10-14-previous-samples](archive/2026-10-14-previous-samples/README.md). Their pre/post archive SHA-256 hashes were verified. The primary project archive also contains the earlier ZIP. Those samples used different prompts and constraints.

## Recheck or resume

Run `python3 validate_samples.py --write-report --write-comparisons` to recheck the saved files and refresh comparison pages. Every planned case has been attempted. See [resume.md](resume.md) for archived cases and notes on any future user-requested retry.

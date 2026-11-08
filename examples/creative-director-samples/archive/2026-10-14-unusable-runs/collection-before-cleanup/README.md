# Creative Director keyword-only samples

Collected from Hugging Face on 14 October 2026 (Asia/Hong_Kong). The CD expands keywords into a meaningful poster concept for the VP. The user supplies no additional constraints.

## Results

All **45 planned combinations have been attempted**. The collection contains **31 usable JSON briefs**, 13 truncated provider responses, and one provider failure without a response. A full set of 45 usable briefs has not been obtained.

| Prompt type | Qwen3-4B-Instruct-2507 | Qwen3-1.7B | Qwen3.5-4B |
| --- | --- | --- | --- |
| [Message-led](comparisons/message-led.md) | 5/5 valid | 5/5 valid | 1 valid, 4 truncated |
| [Visual metaphor](comparisons/visual-metaphor.md) | 5/5 valid | 5/5 valid | 5 truncated |
| [Human story](comparisons/human-story.md) | 5/5 valid | 5/5 valid | 4 truncated, 1 provider error |

The user first paused Qwen3.5 after three outcomes, then explicitly resumed the 12 missing cases. In that resumed batch, 11 responses finished with `finish_reason: length` and empty final content. The human-story AI/creativity/identity case failed twice: first the provider reported that the model produced no output, then the retry reported a network error. Its exact requests and error records are saved, but there is no invented raw response or JSON file.

The earlier Qwen3.5 ocean/message-led output contains incomplete JSON; its home/message-led output is valid. The remaining 30 valid outputs from the other models are preserved unchanged. All 31 usable briefs parse as JSON, match the schema, preserve the exact keywords, and match their captured API content. These checks do not assess creative quality.

Provider defaults were retained. The model-page widget does not expose controls to raise the generation limit or disable thinking. Obtaining usable replacements for failed Qwen3.5 cases requires a further run with suitable inference settings; the current files document the actual outcomes without repairs.

## Read the samples

- [Message-led comparison](comparisons/message-led.md)
- [Visual-metaphor comparison](comparisons/visual-metaphor.md)
- [Human-story comparison](comparisons/human-story.md)
- [Validation report](validation-report.md)
- [Detailed collection status and fidelity audit](collection-status.json)

`outputs/<prompt-type>/<keyword-set>/<model>` groups each sample's files:

- `.request.txt`: exact text submitted in the Hugging Face widget.
- `.raw.txt`: exact final model content, including empty/truncated results.
- `.json`: parsed content with indentation only, present when parsing succeeded.
- `.response.json` or `.api-response.json`: the complete captured API response displayed by the widget.
- `.reasoning.txt`: reasoning text, where separately captured; full provider data remains in the API response.
- `.metadata.json`: model, provider, source, capture time, finish status, and input adaptation.
- `.error.json`: observed UI/provider error when no response could be captured.
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

Sampling parameters, exact checkpoint revisions, and quantization were not exposed by the widget. These are exploratory outputs, not a controlled model benchmark. Recorded failed network calls, superseded whitespace attempts, and capture retries are retained under `attempts/` or adjacent `.error.json` records and excluded from completed sample counts.

## Previous collection

Earlier samples and supporting files are preserved under [archive/2026-10-14-previous-samples](archive/2026-10-14-previous-samples/README.md). Their pre/post archive SHA-256 hashes were verified. The primary project archive also contains the earlier ZIP. Those samples used different prompts and constraints.

## Recheck or resume

Run `python3 validate_samples.py --write-report --write-comparisons` to recheck the saved files and refresh comparison pages. Every planned case has been attempted. See [resume.md](resume.md) for the remaining unusable cases and requirements for a future retry.

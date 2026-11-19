# Unified local configuration

User-approved design: one local TOML containing all four agents, paths/fonts/runtime/budget and plaintext API keys. Commit the complete example with empty keys; ignore the actual local file. Preserve existing per-agent files and commands. No push or live model calls in this phase.

The unified runner loads strict typed sections and resolves file paths against the configuration directory. Explicit mode/root/keywords CLI options may override central settings; legacy per-agent flags remain available without --config. Do not modify native agent files during a unified run. Pass keys privately to clients, never change global environment or serialize credentials into registration metadata. Config snapshots and validation/parse errors must redact values.

Use stdlib tomllib and existing Pydantic. A1 adds optional client/model parameters retaining old defaults. A4 already accepts a private key constructor argument. Cloud A3 adds the same optional argument while retaining its environment-key fallback. A2/fonts/budget are passed to existing constructors.

Verify blank-key offline execution, custom fonts/paths/budgets/parameters, strict validation, missing live keys before calls, key isolation/redaction in HTTP mocks and persisted files, and all previous independent entry tests.

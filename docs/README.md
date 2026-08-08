# WindowsPet Design Documents

This directory is the **source of truth** for WindowsPet product and implementation specifications.

## Canonical documents

- `WindowsPet_設計仕様書.md` — product identity, autonomy, Local-first intelligence, model escalation, safety, roadmap and governance.
- `WindowsPet_AI_Memory_Learning_Spec.md` — Local AI, Memory, Reflection, Skills, Global Brain / shared learning, Luna→Terra→Sol routing.
- `WindowsPet_PowerShell実行設計.md` — safe local PowerShell execution, confirmation, one-shot grants, UAC elevation, verification and audit.
- `WindowsPet_キャラクター・アニメーション仕様.md` — character package, animations, runtime states and editor behavior.

## Rules

1. Read these Git documents before making WindowsPet architecture or implementation decisions.
2. Keep canonical filenames stable. Do not create a new filename for every version.
3. Update the `Version` and revision history inside each document and rely on Git history for old versions.
4. If ChatGPT Project shared storage contains an older copy, **Git `main` wins**.
5. ChatGPT Project shared storage should contain only a short pointer memo to this directory, not duplicated specification files.
6. Before implementation, inspect the current repository HEAD and compare it with the relevant latest specs.
7. Do not reference old version-numbered specification filenames; use the fixed canonical filenames and Git history instead.

## Product shorthand

WindowsPet is a **PC-resident AI partner and “strongest IT administrator”**: quiet and respectful in normal use, locally intelligent by default, capable of understanding the user's underlying goal from permitted PC context, able to research unknown problems, use Luna/Terra/Sol as internal cognitive extensions when needed, perform confirmed real work, verify outcomes, reflect on experience, and share only privacy-safe generalized IT knowledge with other pets.

Current local foundation includes Personal Memory, deterministic Reflection/Revalidation, and the bounded Research Orchestrator under `src/windows_pet/research/`. Its Web/LLM providers are Fake/Protocol boundaries only; real Web, OpenAI reflection, Cloud, UAC, and administrative changes remain intentionally unexecuted.

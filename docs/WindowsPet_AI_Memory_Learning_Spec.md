# WindowsPet AI Memory / Learning / Personality / Shared Knowledge Specification

Version: 0.6\
Date: 2026-08-08\
Status: Design baseline / implementation reference
Related: `WindowsPet_設計仕様書.md`

## 1. Purpose

WindowsPet is not intended to be only a chat UI that forwards every request to an LLM. The product should become a desktop AI pet that:

- learns from successful user actions,
- becomes faster by reusing previously learned actions locally,
- shares safe, abstracted common knowledge with other WindowsPet instances,
- develops user-specific memories and habits,
- gradually adapts its speaking style and relationship distance,
- proactively talks at appropriate moments,
- forgets low-value information instead of retaining unlimited raw history,
- can use an external LLM only when local or shared knowledge is insufficient,
- can research unknown problems using local inspection, official sources and Web search,
- can synthesize a new procedure, test it safely, re-plan when it fails, and learn the verified result.

The core product concept is:

> Shared intelligence grows globally, while each pet grows into a unique partner for its user.

## 2. Design principles

1. Fast local execution first.
2. Personal memory stays local by default.
3. Only abstracted, non-sensitive common knowledge is shared.
4. Raw experience is temporary; distilled knowledge is long-lived.
5. The pet should learn, reinforce and forget in a human-like way.
6. Personality and relationship distance should evolve gradually.
7. Changes that may feel intrusive, especially casual speech, require user permission.
8. Proactive speech must consider timing and annoyance risk, not only clock-based rules.
9. Cloud provider dependencies must be isolated so Google Cloud can later be replaced by AWS.
10. LLM usage should become less frequent as knowledge accumulates.
11. Unknown tasks are research problems, not automatic failures.
12. The pet should autonomously perform safe/read-only investigation and ask only at side-effect or genuine preference boundaries.
13. Web content is untrusted evidence; it may inform a plan but never grants execution authority.
14. Successful newly discovered procedures should be distilled into reusable Skills after verification.
15. Learned confidence never overrides safety policy or confirmation requirements.

## 3. High-level architecture

```text
User input / PC context
        |
        v
+-------------------------+
| WindowsPet local agent  |
+-------------------------+
 | 1. Personal Memory
 | 2. Local Skills
 | 3. Built-in Skills
 | 4. Shared Knowledge cache
 | 5. Intent resolver
 | 6. Proactive speech engine
 | 7. Personality / relationship engine
        |
        | unresolved only
        v
+-------------------------+
| Global Brain API        |
| Google Cloud initially  |
+-------------------------+
        |
        | unresolved only
        v
+-------------------------+
| External / Local LLM    |
+-------------------------+
```

Recommended resolution priority:

```text
Personal Memory
-> Local Skills
-> Built-in Skills
-> Local cache of Shared Knowledge
-> Global Brain
-> LLM
```

## 3A. Unknown-task research and skill acquisition

When no known Skill resolves the user's goal, WindowsPet should enter a research loop rather than immediately returning the problem to the user.

```text
Unknown goal
-> inspect local state and capabilities
-> search Personal / Local / Built-in / Shared knowledge
-> search official documentation when needed
-> search the wider Web when needed
-> LLM synthesizes candidate procedure
-> validate assumptions against the local PC
-> run read-only tests automatically
-> request confirmation at the first side-effect boundary
-> execute exact approved action
-> verify desired state
-> if failed, re-plan from observed evidence
-> distill successful procedure into Local Skill / shareable abstraction
```

The system should preserve provenance for learned procedures: where the idea came from, which local facts were verified, which action actually succeeded, and what verification proved success. A website instruction or LLM suggestion is not considered learned knowledge until WindowsPet has validated it against policy and observed a successful result.

For time-sensitive software, driver, Windows, API, security, or vendor-specific information, current official information should outrank stale cached or model knowledge.

## 4. Local learning and execution cache

### 4.1 Goal

Commands already resolved successfully should not require an LLM again when the same or sufficiently similar request is received.

Example:

```text
User: "メモ帳を起動して"
First time:
  unresolved -> LLM / resolver -> launch_app:notepad -> success -> learn

Next time:
  local match -> launch_app:notepad -> execute immediately
```

### 4.2 Learned action record

Suggested conceptual fields:

```json
{
  "skill_id": "launch_app:notepad",
  "intent": "launch_app",
  "target_type": "windows_builtin",
  "target": "notepad",
  "aliases": [
    "メモ帳を起動して",
    "メモ帳開いて",
    "notepad開いて"
  ],
  "success_count": 123,
  "failure_count": 1,
  "last_used_at": "...",
  "memory_strength": 0.98,
  "scope": "global_eligible"
}
```

Do not store machine-specific absolute paths in shared knowledge unless they are standardized and safe. Local resolution should convert abstract targets into executable paths or commands for that PC.

## 5. Memory model

WindowsPet should use multiple kinds of memory instead of one unlimited conversation history.

### 5.1 Short-term memory

Examples:

- current conversation context,
- recently opened app,
- recently accessed file/folder,
- current task,
- temporary references such as "that file" or "the previous one".

Characteristics:

- short retention,
- high recency weight,
- automatically expires,
- should not become long-term memory unless reinforced or explicitly saved.

### 5.2 Long-term personal memory

Examples:

- frequently used applications,
- preferred tools,
- preferred wording/tone,
- common working hours,
- recurring workflows,
- user-defined facts explicitly marked "remember this".

Characteristics:

- local by default,
- survives restart,
- weighted by importance, frequency and recency,
- can be explicitly forgotten by the user.

### 5.3 Habit memory

The system may infer patterns from repeated behavior.

Example raw experiences:

```text
Mon 08:35 -> Outlook opened
Tue 08:32 -> Outlook opened
Wed 08:38 -> Outlook opened
```

Distilled habit:

```text
"User often opens Outlook shortly after starting work in the morning."
```

After successful consolidation, fine-grained historical events can be reduced or deleted.

### 5.4 Explicit protected memory

When the user says equivalents of:

- "覚えておいて"
- "忘れないで"

store the corresponding memory as protected/high-priority unless it violates safety/privacy policies.

When the user says:

- "忘れて"
- "この記憶を消して"

remove or disable the relevant memory promptly.

## 6. Human-like forgetting and consolidation

### 6.1 Principle

Do not retain unlimited raw events. Retain useful knowledge distilled from those events.

```text
Raw Experience
     |
     v
Pattern detection
     |
     v
Knowledge / habit / preference
     |
     v
Old raw events deleted or compacted
```

### 6.2 Memory strength

Each non-protected memory should have a strength score influenced by:

- usage frequency,
- successful reuse,
- recency,
- explicit user confirmation,
- uniqueness/usefulness,
- conflicts or failures.

Frequently reused memories become stronger. Rarely used memories decay.

### 6.3 Cleanup policy

Candidate policies:

- short-term events: hours to days,
- raw action logs: limited retention window,
- consolidated habits: retained while useful,
- low-strength stale memories: delete/compact,
- protected memories: no automatic deletion unless explicitly configured.

Exact retention periods should be configurable and finalized during implementation.

## 7. Global Brain / shared knowledge

### 7.1 Purpose

WindowsPet instances should share generalizable knowledge so that learning by one user can benefit others.

Examples suitable for sharing:

```text
"メモ帳を開いて" -> launch_app:notepad
"Bluetooth設定を開いて" -> open_windows_settings:bluetooth
"音量を少し上げて" -> volume_change:+small
```

### 7.2 Never share by default

Do not upload raw personal content such as:

- local user names,
- personal file paths,
- private filenames,
- email addresses,
- credentials,
- private IP addresses / internal servers,
- full conversations,
- screenshots,
- document contents,
- application secrets,
- personal schedules unless separately opted in for backup/sync.

### 7.3 Shared record should be abstracted

Preferred shared representation:

```json
{
  "intent": "launch_app",
  "target_type": "windows_builtin",
  "target": "notepad",
  "aliases": ["メモ帳を開いて", "メモ帳起動"],
  "success_count": 18421,
  "failure_count": 23,
  "confidence": 0.998
}
```

### 7.4 Trust and promotion

A newly reported mapping should not immediately become trusted global knowledge.

Suggested lifecycle:

```text
Candidate
-> Observed by multiple clients
-> Validated by success statistics
-> Trusted shared knowledge
-> Built-in candidate (optional future release)
```

Use success/failure counts, unique-client evidence, version/OS compatibility and abuse controls.

## 8. Three-layer shared knowledge model

### Built-in

Knowledge shipped with WindowsPet.

- basic Windows commands,
- standard OS actions,
- safe core skills.

### Community / Global

Knowledge learned and validated through the server.

- command aliases,
- common app operations,
- successful abstract action patterns.

### Personal

Knowledge unique to the user or PC.

- preferred editor,
- frequently used folders,
- local application paths,
- personal habits,
- conversation style preferences.

Priority should normally be:

```text
Personal -> Built-in -> Community -> LLM
```

The exact ordering between Built-in and Community may be adjusted where safety requires Built-in rules to override server data.

## 9. Personality and relationship system

### 9.1 Initial state

The pet starts as a first-time acquaintance.

Default behavior:

- polite Japanese,
- respectful distance,
- no assumed nickname,
- no casual/tameguchi speech unless explicitly configured.

Example:

```text
"はじめまして。これからよろしくお願いします。"
```

### 9.2 Relationship growth

Maintain an internal relationship/familiarity state based on:

- days used,
- meaningful interactions,
- response frequency,
- successful assistance,
- user preference feedback.

Possible internal stages:

```text
first_meeting
acquainted
comfortable
partner
long_term_partner
```

These do not need to be exposed as game-like numeric levels unless a future UI intentionally uses them.

### 9.3 Speech adaptation

The pet may gradually learn:

- preferred message length,
- formality,
- emoji usage,
- response tempo,
- directness,
- humor tolerance,
- preferred encouragement style.

The goal is not to imitate the user exactly, but to adapt toward a communication style the user appears comfortable with.

### 9.4 Permission for casual speech

Do not automatically switch from polite language to tameguchi only because familiarity increases.

At an appropriate relationship stage, ask naturally once, for example:

```text
"だいぶ慣れてきましたね。もう少しくだけた話し方にしてもいいですか？"
```

Store the answer as a durable preference.

Possible outcomes:

- casual speech allowed,
- keep polite speech,
- more casual preferred,
- ask later.

## 10. Proactive speech

### 10.1 Goal

WindowsPet should sometimes speak without being prompted, like a pet living on the desktop.

Possible triggers:

- app/PC startup,
- morning/afternoon/evening,
- before lunch break,
- before end of workday,
- after returning from long idle,
- after long continuous PC usage,
- weekday-specific context,
- repeated routine detection,
- lightweight random conversation.

Examples:

```text
Morning startup:
"おはようございます！"

Before known lunch break:
"もうすぐお昼休みですね。あと少しです！"

After idle return:
"おかえりなさい。"

Occasional conversation:
"今日は調子どうですか？"
```

### 10.2 Speak decision before content generation

The system must first decide whether it is appropriate to speak.

Conceptual score:

```text
startup event                  +40
lunch soon                     +30
long idle return               +25
recent proactive speech        -50
user appears focused           -40
user often responds positively +10
late night                     -30
```

Speak only when the score crosses a threshold and cooldown constraints are satisfied.

### 10.3 Anti-annoyance controls

Required controls:

- minimum cooldown,
- daily/period frequency cap,
- quiet hours,
- focus mode suppression,
- user-configurable proactive speech level,
- learn from repeated ignoring/dismissal,
- never interrupt critical/system-sensitive operations.

Repeatedly ignored categories should decay in frequency.

### 10.4 Variation

Do not emit exactly the same phrase at the same time every day. Use phrase families and context-aware generation while keeping latency and API cost low.

## 11. Learning from user reactions

The pet may learn from interactions such as:

- user replies,
- ignores,
- dismisses,
- immediately performs suggested action,
- explicitly says "don't say that" / "I like that",
- changes a preference.

Negative feedback should reduce future use of the associated behavior. Explicit user instructions override inferred behavior.

## 12. Local AI, cognitive extension and model routing

### 12.1 Intelligence identity

The user-facing intelligence is always **WindowsPet**. Local models, deterministic logic, Luna, Terra, Sol, Web search, and shared knowledge are internal cognitive resources. They must not become separate conversational personas unless a future product mode explicitly introduces that concept.

WindowsPet's default relationship to the user is a respectful partner. It may become friend-like as the relationship develops, but it should not force familiarity. Its technical behavior should resemble an exceptionally capable IT professional: quiet when not needed, decisive and thorough when asked for help.

### 12.2 Local-first Primary Brain

The Primary Brain should run locally as much as practical. It may consist of multiple components rather than one model:

- deterministic routing and policy logic,
- local intent classification,
- local Skill and Memory retrieval,
- a lightweight/local LLM when beneficial,
- context filtering and summarization,
- confidence estimation,
- execution/verification controllers.

Routine interaction, known Skills, time-based reactions, local retrieval, and simple interpretation should not require a cloud model. Fast local acknowledgement should occur before a cloud round-trip when possible.

### 12.3 External cognitive-extension hierarchy

```text
Local Primary Brain
  -> Luna  : default external reasoning
  -> Terra : escalation when expected resolution quality materially improves
  -> Sol   : highest-capability escalation when expected improvement justifies cost/latency
```

Rules:

1. Do not call all three models mechanically.
2. Luna is the normal external model.
3. Escalate only when evidence suggests that the next model is likely to improve the chance of resolution, safety of reasoning, or quality enough to justify latency/cost.
4. A task may skip directly to Terra when historical evidence or task complexity makes Luna clearly unsuitable.
5. Sol is reserved for genuinely hard cases where its expected value is high.
6. Record non-sensitive routing outcomes so future model selection can learn from task type, failure mode, latency, token cost and verified resolution.
7. Model escalation never weakens Policy Gate, confirmation or execution controls.

### 12.4 User-facing experience

Do not normally say "I will ask Luna/Terra/Sol". Use the pet's own state:

- `thinking`: "ちょっと考えます…"
- `researching`: "調べてみますね！"
- deeper analysis: "もう少し詳しく見てみます。"

Cloud AI use must still be disclosed in settings/privacy information, including what kinds of context may be sent and how users can control it. Unified identity is a UX principle, not permission to conceal data handling.

### 12.5 Intent understanding from PC context

WindowsPet may use relevant, permitted local-PC facts to infer the user's actual goal. The inference process should combine:

```text
User wording
+ current local state
+ recent task context
+ relevant personal memory
+ known workflows
= goal hypothesis
```

The result is a **hypothesis**, not a fact. If different plausible intents would cause materially different changes, ask. Otherwise, safe read-only investigation may proceed to reduce uncertainty. Do not infer clinical/psychological diagnoses from behavior.

### 12.6 Reflection pipeline

After meaningful tasks, especially new or failed-then-successful tasks, use:

```text
Experience
-> Reflection
-> Learning
-> Knowledge / Skill candidate
-> Revalidation
-> Local reuse / Global Brain candidate
```

Reflection should consider:

- the user's real goal and completion condition,
- observations and evidence,
- hypotheses that were right or wrong,
- failed steps and likely reasons,
- unnecessary questions or tool calls,
- what verified success,
- how to solve the next similar task faster,
- whether Luna/Terra/Sol routing was appropriate,
- what can be generalized without exposing personal/device-specific data.

Do not turn raw private conversation or raw machine logs into shared knowledge.

### 12.7 Collective intelligence boundary

Multiple pets may become collectively smarter through the Global Brain, but shared knowledge must represent generalized IT experience rather than user surveillance.

Good shared knowledge:

- product/version-specific failure pattern,
- validated troubleshooting sequence,
- compatibility condition,
- reusable verification method,
- abstract Skill template.

Local-only by default:

- personal conversation,
- user's communication preferences,
- personal habits,
- exact personal paths,
- private files,
- account identifiers,
- secrets and credentials,
- raw PC activity history.

Every pet revalidates shared knowledge against its own local environment before relying on it.

## 13. Initial Google Cloud architecture

Initial cloud provider: Google Cloud.

Recommended minimal architecture:

```text
WindowsPet
    |
    | HTTPS
    v
Cloud Run
  FastAPI
    |
    v
Repository interface
    |
    +--> Firestore (initial option)
```

Responsibilities:

### Cloud Run / FastAPI

- Global Brain API,
- knowledge lookup,
- candidate submission,
- result statistics,
- validation/promotion logic,
- future account/sync endpoints.

### Firestore

Initial storage candidates:

- global skills,
- aliases,
- compatibility metadata,
- aggregate statistics,
- candidate knowledge,
- knowledge version metadata.

Do not let application/business logic call Firestore directly throughout the codebase.

## 14. Cloud portability requirement

Google Cloud is the starting platform, but AWS migration must remain practical.

Use an abstraction such as:

```text
API / Service layer
        |
        v
SkillRepository
MemoryRepository
StatisticsRepository
        |
        +--> Firestore implementation
        +--> future DynamoDB implementation
```

Avoid spreading code like direct `firestore.collection(...)` calls across business logic.

Potential future mapping:

```text
Google Cloud Run   -> AWS App Runner / ECS / Lambda
Firestore          -> DynamoDB or RDS depending on final data model
Cloud Storage      -> S3
Google auth option -> Cognito or another identity provider
```

Cloud-specific details must live behind adapters/repositories where practical.

## 15. Data model domains

Recommended logical domains:

### skills

Canonical executable abilities.

### aliases

Natural-language expressions that resolve to a skill.

### execution_results

Temporary or aggregated success/failure evidence.

### global_statistics

Usage, success rates, compatibility and confidence.

### personal_memory

Local-only user-specific memories by default.

### habits

Locally inferred behavioral patterns.

### personality_preferences

Formality, talk frequency, accepted casual speech, tone preferences, etc.

### relationship_state

Internal familiarity state and milestones.

### proactive_speech_state

Cooldowns, last speech time, suppression state and learned response rates.

## 16. Privacy and consent

Privacy should be part of the architecture, not an afterthought.

Required principles:

1. Personal memory remains local by default.
2. Shared learning uploads only abstract/generalizable knowledge.
3. Remove or reject potentially identifying values before upload.
4. Cloud backup/sync of personal memory must be an explicit opt-in feature.
5. Users need controls to inspect/delete learned personal information.
6. Explicit "forget" instructions must be honored.
7. Server telemetry retention must be bounded.
8. Raw conversation text should not become Global Brain training data by default.

## 17. Storage growth strategy

The system should grow knowledge, not indefinitely grow raw history.

### Server

```text
Raw reports/events (short retention)
        |
        v
Aggregation / validation
        |
        v
Knowledge records + statistics
        |
        v
Delete or compact old raw events
```

### Local

```text
Recent experiences
   -> consolidate into preferences/habits/skills
   -> decay low-value memories
   -> delete stale details
```

Images, audio, screenshots and full file contents must not be retained merely for behavioral learning unless a separate feature explicitly requires them.

## 18. Suggested implementation phases

### Phase 1 - Local fast learning

- deterministic local command registry,
- local learned command DB,
- alias matching,
- success/failure tracking,
- reuse known commands without LLM,
- memory strength / last-used fields.

Current implementation: WindowsPet has a deterministic built-in application-launch registry and a local SQLite Skill store. Learned aliases store only an abstract intent/target and normalized alias; they never store conversation text, secrets, or machine-specific executable paths. A learned hit still enters the existing resolver, policy, confirmation, one-shot Grant, executor, and verification flow.

### Phase 2 - Personal memory

- short-term and long-term distinction,
- explicit remember/forget,
- frequently used app/folder learning,
- basic memory decay,
- memory inspection UI.

Current implementation: Personal Memory is a separate local-only domain under `src/windows_pet/memory/`, with a repository interface and SQLite adapter. It supports short-term TTL, long-term and protected records, explicit structured remember/forget, bounded lookup, deterministic privacy rejection, reinforcement, cleanup candidates, corrupt-database safe fallback, and a small inspection/deletion UI. No HTTP, cloud, OpenAI upload, or Global Brain call is made. Reflection foundation is also local and deterministic: structured Experience -> Reflection -> provenance-bearing verified LearningCandidate -> abstract Local Skill promotion -> current resolver revalidation. Unverified execution, cancellation, policy rejection, secrets, raw logs, raw conversation, and machine-specific paths are never promoted. Global Brain and cloud sharing remain unimplemented.

### Research Orchestrator and model boundaries

Current implementation: `src/windows_pet/research/` provides immutable `ResearchGoal`, bounded `ResearchSession` state transitions, a code-owned Capability Registry, provenance-bearing Evidence with fixed trust ordering, bounded local read-only investigation, structured CandidatePlan validation, confirmation waiting, cancellation, safe failure evidence, bounded re-planning, and deterministic Reflection handoff. Known Local Skills resolve on the fast path and do not call a research or reasoning provider. Action steps are proposals only; the Orchestrator has no direct process, service, shell, PowerShell, registry, file-write, network, or cloud executor.

External research and reasoning are represented by provider protocols and Fake providers. Web evidence is untrusted, provider input is bounded/sanitized, and arbitrary shell or download-and-execute plans are rejected by local policy. The `ModelRouter` selects Local/Luna/Terra/Sol heuristically: Local first, Luna default external, Terra after an eligible failure, and Sol only for hard high-value cases within latency/cost budgets. Routing never changes confirmation or safety policy. `DeterministicReflection` remains authoritative for promotion; `LLMReflectionProvider` is an optional structured enrichment interface and cannot promote a Skill. Automated validation keeps real Web/OpenAI/Cloud calls at zero.

### Phase 3 - Global Brain MVP on Google Cloud

- FastAPI on Cloud Run,
- Firestore repository adapter,
- global skill lookup,
- candidate upload,
- abstract/sanitized records only,
- success/failure aggregation,
- local cache of trusted Global Brain knowledge.

### Phase 4 - Proactive pet behavior

- startup greetings,
- time/context triggers,
- lunch/work-time awareness,
- cooldown and quiet periods,
- response-based frequency learning.

### Phase 5 - Personality / relationship growth

- initial polite personality,
- familiarity state,
- speech-style preference learning,
- permission flow before casual speech,
- user-adjustable personality controls.

### Phase 6 - Habit consolidation / forgetting

- repeated-event pattern detection,
- consolidate raw events into habits,
- memory-strength decay,
- pruning/compaction jobs.

### Phase 7 - Optional local LLM

- add local model only where it provides value,
- preserve deterministic fast path,
- benchmark latency and resource usage.

### Phase 8 - Scale / AWS migration if justified

- migrate repository adapters,
- export/import Global Brain data,
- switch API infrastructure with minimal WindowsPet client impact.

## 19. Critical safety rules for executable actions

Learned execution creates security risk and must not bypass safety controls.

At minimum classify actions by risk.

### Low risk / reusable

- opening standard applications,
- opening user-approved folders,
- navigation to settings,
- non-destructive UI actions.

### Confirmation or stricter validation required

- deleting/moving files,
- changing security settings,
- installing/uninstalling software,
- registry/system configuration changes,
- shell commands with broad effects,
- credential-related operations,
- network/security administration.

A high success rate must never automatically turn a dangerous operation into an unconfirmed action. Safety policy overrides learned confidence.

## 20. Definition of the WindowsPet experience

The intended experience is:

1. At first, the pet is polite, respectful and behaves like a quiet partner living on the PC, while already knowing general Windows skills.
2. When it encounters something unknown, it first investigates what it can safely inspect, researches current information when needed, uses AI to synthesize a procedure, asks the user only at necessary decision/side-effect boundaries, and learns from verified success.
3. Repeated actions become immediate and local; cloud reasoning is avoided when local knowledge is sufficient.
4. Verified, privacy-safe and generalized knowledge contributes to the shared Global Brain so multiple pets become collectively smarter.
5. The Global Brain makes new WindowsPet installations smarter from day one.
6. Personal habits, preferences and memories stay with each individual pet.
7. The pet gradually learns when to speak and when to stay quiet.
8. Its communication style becomes more comfortable for the user over time.
9. It asks permission before crossing important relationship boundaries such as switching to casual speech.
10. Old, unimportant memories fade while important and repeated memories strengthen.
11. Months later, two installations that started identically should behave like meaningfully different individuals.

## 21. Product concept statement

> WindowsPet is a PC-resident AI partner: quiet and respectful in daily life, exceptionally capable when asked for help, locally intelligent by default, able to extend its thinking through Luna/Terra/Sol when needed, and able to turn verified experience into private personal understanding or privacy-safe shared IT knowledge.

## 22. Current decisions fixed by this specification

- Start the Global Brain on Google Cloud.
- Keep the architecture portable for a future AWS migration.
- Prefer Cloud Run + FastAPI for the initial API layer.
- Use a repository/adapter layer rather than direct cloud DB calls in domain logic.
- Shared knowledge and personal memory are separate domains.
- Personal memory is local by default.
- Share only abstracted/generalizable learning data.
- Known/repeated commands should bypass LLM inference.
- Use memory reinforcement, decay, consolidation and forgetting.
- Allow proactive speech, but gate it with context, cooldown and learned user preference.
- Start with polite language as an initial relationship state.
- Ask user permission before switching to casual/tameguchi speech.
- Aim for different long-term personality/behavior per user while retaining a stable core personality.


## 23. Document governance

The canonical copy is `docs/WindowsPet_AI_Memory_Learning_Spec.md` in the `lazylifedev/WindowsPet` Git repository. Keep the filename stable; version through this document and Git history. ChatGPT Project shared storage should hold only a pointer memo to the Git source of truth.

## 24. Revision history

- **0.3 — 2026-08-08:** Added WindowsPet intelligence identity, Local-first Primary Brain, Luna/Terra/Sol cognitive-extension routing, local-PC-assisted intent inference, explicit Reflection pipeline, collective-intelligence privacy boundary, and Git-first documentation governance.
- **0.4 — 2026-08-08:** Recorded the Phase 1 local deterministic launch routing and SQLite Skill store implementation boundary.
- **0.5 — 2026-08-08:** Recorded the local Personal Memory Phase 2 and deterministic Reflection/Revalidation foundation boundaries.
- **0.6 — 2026-08-08:** Recorded the local Research Orchestrator, Capability Registry, bounded Evidence/plan/re-plan flow, Local/Luna/Terra/Sol routing foundation, and optional structured LLM Reflection boundary. Real external providers remain unconnected.

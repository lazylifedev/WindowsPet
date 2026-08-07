# WindowsPet 設計仕様書

**Subtitle:** ユーザーの困りごとを実作業で解決するキャラクター型ローカルPCエージェント  
**Version:** 0.2.0  
**Date:** 2026-08-07  
**Audience:** ChatGPT / Codex / WindowsPet developers  
**Status:** Confirmed local application launch implemented; chat-to-action and PowerShell runtime are next.

> **AIへの最重要指示:** WindowsPetを単なるデスクトップペット、チャットクライアント、読み取り専用検索アプリとして扱わないこと。チャットは指示インターフェースであり、製品本体は、PCや接続先を調査し、副作用のある操作は内容に応じた確認を行い、承認された範囲だけを実行し、結果を検証し、再利用できる知識を記憶するローカルエージェントである。

## 0. Quick reference

- WindowsPetは、ローカルPC、ファイルサーバー、Teams、将来の社内システムで実作業を行うキャラクター型AIエージェントである。
- 基本ライフサイクルは **investigate → plan → confirm → execute → verify → remember**。
- 読み取り専用の検索、調査、診断、状態確認は原則として確認不要。
- アプリ起動、ファイル変更、プロセス停止、設定変更、外部送信などの副作用は確認必須。
- 承認は一回限りで、対象・操作・引数・副作用・実行内容に束縛する。
- Windows操作では、実装速度・保守性・対応範囲に優れる場合、PowerShellを標準的な実行バックエンドとして積極的に利用する。
- AIへ無制限のシェル権限を渡さない。AIは構造化された操作または実行案を提示し、WindowsPetのPolicy Gateと確認画面を通す。
- 単純な`.exe`起動など、専用Executorの方が安全で明確な操作は既存の専用実装を維持する。
- 実行前にユーザーへ表示した内容と、実際に実行する内容は完全に一致しなければならない。
- 実行ファイルの場所や成功した手順など、再利用可能な発見はローカルへ保存し、利用前に安価な再検証を行う。
- 共有可能な知識だけを、秘密情報・個人情報・端末固有情報を除去した上でchat-serverへ共有する。
- Gitリポジトリ内の文書を正本とし、ChatGPTプロジェクト共有ストレージには参照用コピーを置く。

## 1. Product definition

WindowsPetは、会話を入口としてユーザーの目的を理解し、ローカルPC、ファイルサーバー、Teams、社内システムを調査・操作するエージェントである。単に操作方法を説明するだけでなく、可能な範囲では実際に作業し、結果を確認し、次回以降に再利用できる知識を保存する。

### User value

- ユーザーは実行ファイルの場所、設定画面、PowerShellコマンド、管理ツールを覚える必要がない。
- 曖昧な困りごとから原因を調査し、具体的な解決案を準備できる。
- 副作用は必ずユーザー確認を通すため、最終的な実行権限はユーザーが持つ。
- 過去に確認済みの場所や手順を再利用し、調査時間とAPI利用量を削減できる。
- 将来的に複数端末・複数ユーザーで、安全な組織知識を共有できる。

## 2. Non-negotiable principles

1. **実作業で解決する。** 実行可能なタスクを説明だけで終わらせない。
2. **実行権限はユーザーが持つ。** 副作用のある操作は確認を必須とする。
3. **確認方法は操作に応じて変える。** 単純確認、差分、影響範囲、送信内容などを適切に表示する。
4. **承認範囲を拡張しない。** 対象、引数、実行内容、権限、影響が変わったら再確認する。
5. **PowerShellは実行手段であり、権限境界ではない。** Policy Gate、確認、Grant、検証を省略しない。
6. **専用ToolとPowerShellを使い分ける。** 安全性・明確性に優れる場合は専用Executorを維持し、Windows管理操作ではPowerShellを優先する。
7. **記憶でコストと待ち時間を減らす。** ただし利用前に再検証する。
8. **秘密情報をモデル、ログ、共有記憶へ出さない。**
9. **コマンド終了ではなく、ユーザーの希望状態を検証する。**
10. **提案、承認、実行、検証、記憶更新を監査可能にする。**

## 3. Task lifecycle

```text
User request
  -> understand goal, target, constraints, completion condition
  -> retrieve session, local, and shared memory
  -> investigate only missing information
  -> select dedicated Tool or PowerShell backend
  -> prepare an ActionProposal and exact preview
  -> deterministic policy validation
  -> request context-sensitive confirmation
  -> issue a one-shot, scope-bound ExecutionGrant
  -> execute only the approved scope
  -> verify the resulting state
  -> report success, failure, and actual changes
  -> store reusable knowledge locally and optionally share it
```

## 4. Confirmation model

### Read-only actions

検索、一覧、メタデータ取得、診断、状態確認など、状態を変更しない操作は原則として確認不要。ただし、秘密情報や個人情報を含む可能性がある読み取りは、表示範囲・送信範囲の制限を別途適用する。

### Side-effecting actions

次は必ず確認する。

- アプリ起動
- ファイル作成、編集、移動、改名、削除
- プロセスやサービスの停止・再起動
- ソフトウェアのインストール・アンインストール
- Windows設定、レジストリ、タスクスケジューラ、スタートアップの変更
- UI自動操作による状態変更
- メール、Teams、アップロードなどの外部送信
- PowerShellによる状態変更

### Confirmation types

| Type | Examples | Show the user | Actions |
|---|---|---|---|
| A. Simple Yes/No | Launch app, stop process | Target, action, impact, privilege | Execute / Cancel |
| B. Before/after or diff | Rename, edit document, modify setting | Before, after, diff, backup | Save/Execute / Revise / Cancel |
| C. Plan and impact | Repair issue, multiple settings | Cause, steps, impact, admin need, rollback | Execute all / Select / Cancel |
| D. External-send preview | Teams, email, upload | Recipient, body, attachments, audience | Send / Revise / Cancel |
| E. Install/uninstall | winget install/remove | Product, source, package ID, privilege, restart | Execute / Cancel |
| F. PowerShell script review | Free-form or advanced PowerShell | Purpose, exact script, target, privilege, timeout, expected changes, verification | Execute exact script / Revise / Cancel |

承認は一回限りで、ActionProposalのfingerprint、ConfirmationSession、ExecutionGrantへ束縛する。表示後に対象・スクリプト・引数・作業フォルダー・権限・副作用が変わった場合は実行せず、再確認する。

## 5. Architecture

```text
Character UI / Chat
    -> Agent Orchestrator
       - intent and goal parser
       - memory retriever
       - planner
       - tool selector
       - result verifier
    -> Policy & Confirmation Gate
       - ActionProposal validation
       - context-sensitive confirmation
       - one-shot ExecutionGrant
    -> Local Tool Runtime
       - discovery/search
       - dedicated app/process launcher
       - file operations
       - PowerShell execution backend
       - package manager
       - Windows administration
       - UI automation/screen inspection
    -> Local Memory + Audit Log
    -> chat-server Shared Backend
       - shared memory
       - agent/device registry
       - connector gateway
           - file server
           - Teams
           - future systems
```

LLMは任意のOS操作を直接実行しない。LLMは構造化されたTool callまたはPowerShell実行案を返し、決定論的なPolicy Gateが副作用、権限、確認形式、対象範囲を検証する。ユーザー承認後にのみ一回限りのExecutionGrantを発行する。

## 6. Tool and execution contract

すべてのTool／Executorは最低限、次を持つ。

- name / version / operation
- structured inputs
- side-effect classification
- reversibility
- admin requirement
- preview model and confirmation type
- timeout and cancellation behavior
- execution backend
- verification method
- audit fields
- memory update rule

### Execution backend selection

```text
単純な.exe起動
  -> 専用ApplicationLaunchExecutor（subprocess.Popen、shell=False）

Windows管理、サービス、レジストリ、winget、イベントログ、ネットワーク調査
  -> PowerShellを優先

ファイル操作
  -> 専用ToolまたはPowerShell。差分・ロールバック・検証の明確な方を選ぶ

特殊なOS操作
  -> Windows API、専用Helper、UI Automationを選択
```

PowerShellの詳細は `WindowsPet_PowerShell実行設計_v0.1.0.md` を正本とする。

## 7. PowerShell policy summary

- PowerShellは標準的な実行バックエンドとして使用できる。
- 構造化された定型操作を優先し、必要な場合はAIが自由形式スクリプトを提案できる。
- 自由形式スクリプトは全文表示と強い確認を必須とする。
- 確認されたスクリプトと実行されるスクリプトをSHA-256で束縛する。
- `shell=True`、`cmd.exe`経由、`-EncodedCommand`、難読化、表示していない追加コマンドは禁止する。
- 標準ユーザーで実行し、管理者操作は専用の昇格経路と別確認を必要とする。
- 出力、終了コード、対象状態を検証し、実行結果だけで成功扱いにしない。
- スクリプト本文、秘密情報、個人情報を通常の監査ログへ保存しない。監査はハッシュと安全なメタデータを中心とする。

## 8. Memory architecture

### Memory levels

1. **Working memory:** 現在の目標、計画、候補、確認状態、中間結果。
2. **Local persistent memory:** 実行ファイル、インストール済みアプリ、共通フォルダー、端末情報、成功・失敗した手順、ユーザー設定。
3. **chat-server shared memory:** 組織共通のアプリ配置、標準手順、ファイルサーバー構成、共通エラーと検証済み解決策。

### Store

- アプリ名、実行ファイル、ショートカット、package ID、version。
- 共通フォルダー、ファイルサーバーの論理名と用途。
- 再利用可能なPowerShellテンプレート、前提条件、検証方法。
- 成功した手順、失敗した方法、失敗理由。
- 表示、バックアップ、確認詳細に関するユーザー設定。

### Do not store

- APIキー、パスワード、アクセストークン、Cookie、秘密鍵、Credential Managerの値。
- 個人ファイルの全文や個人チャットの全文。
- 一時PID、一時座標、一時ファイル。
- PowerShellへ埋め込まれた秘密値。
- ユーザーが記憶しないよう指定した情報。

### Memory record fields

`id, scope, subject_type, subject_id, key, value, source, confidence, verified_at, expires_at/validation_rule, sensitivity, shareable, device_fingerprint, supersedes, invalid_reason`

### Retrieval and validation

現在タスクの事実、ローカル記憶、共有記憶の順に利用する。存在、version、access、file identityなどを安価に確認し、欠落・期限切れ・不一致の場合だけ再調査する。

## 9. chat-server shared backend

chat-serverは、安全な組織共通知識、agent/device登録、provenance、verification count、freshness、conflictを保持する。端末固有の事実は共有知識より優先する。

次を共有しない。

- 秘密情報
- 個人ファイル内容
- 個人会話内容
- 端末固有の個人パス
- 生のPowerShellスクリプトに含まれる端末固有値や秘密値

共有する手順はテンプレート化し、変数、前提、対象範囲、確認形式、検証方法を含める。

## 10. File server and Teams

### File server

- Search、list、metadata、access check: 原則確認不要。
- Copy、move、rename、edit、delete、ACL変更: 対象と影響を表示して確認。
- ドライブ文字ではなく論理共有名を記憶し、端末固有mappingを分離する。

### Teams

- Search、read、summarize、draft: 原則確認不要。
- Send、reply、post、attach、edit、delete: recipient、audience、body、attachmentsを表示して確認。
- AI生成文を自動送信しない。

## 11. Security and audit

- 標準ユーザーで動作する。
- 管理者権限は承認された操作だけに限定する。
- PowerShellプロファイルを読み込まない。
- 秘密情報はOSのcredential storageから実行時に取得し、モデル入力、確認画面、ログ、共有記憶へ出さない。
- ActionProposal、ConfirmationSession、ExecutionGrant、実行、検証、記憶更新を追跡する。
- 監査ログはtask ID、proposal ID、fingerprint、grant ID、tool、operation、side effect、result code、verification resultを記録する。
- 実行ファイルの生パス、PowerShell本文、例外本文、PID、個人名、PC名、秘密値は通常監査へ保存しない。
- 必要な場合は、アクセス制御・暗号化・保存期間を設けた別の詳細診断ログを明示的に有効化する。

## 12. Representative workflows

### Launch an application from PC inspection

調査済み候補を選択 → `.exe`とfile identityを検証 → ActionProposal → 確認 → Grant → 再検証 → `subprocess.Popen([exact_path], shell=False)` → 短時間検証 → 結果表示。

### Launch an application from chat

ユーザーが「サクラエディタを起動して」 → AIが起動要求Toolを返す → ローカルで候補検索 → 候補が一意なら確認画面、複数なら選択 → Grant → 既存ApplicationLaunchExecutor → 検証。AIは直接`Popen`しない。

### PowerShell read-only inspection

ユーザーの目的を構造化 → 読み取り専用PowerShellを生成または定型Toolへ変換 → Policy判定 → 実行 → JSONまたは構造化出力を検証 → 必要な要約だけをAIへ返す。

### PowerShell state change

調査 → exact script、対象、権限、影響、rollback、verificationをProposalへ固定 → スクリプト全文を表示 → 承認 → Grant → exact hash再検証 → PowerShell実行 → 状態検証 → 結果と実変更を報告。

### Rename a file

対象特定 → conflict/permission確認 → before/after表示 → 確認 → rename → 新旧パス検証 → memory更新。

### Stop an advertisement

画面、通知、process、startup、browser、installed appsを調査 → 原因特定 → 設定・startup・extension・uninstallの変更案を影響とrollback付きで表示 → 承認項目だけ実行 → 広告が止まったことを検証 → 手順を記憶。

## 13. Current implementation state (2026-08-07)

### Implemented foundation

- PySide6透明デスクトップキャラクター、アニメーション、入力・応答バブル。
- OpenAI Responses API、API-key管理、接続確認、会話履歴。
- allowlistされたフォルダーの読み取り専用ファイル検索。
- ローカルPC調査（system、PATH、App Paths、installed apps、Start Menu、winget状態）。
- `ActionProposal`、`PolicyGate`、`ConfirmationSession`、`ExecutionGrant`。
- context-sensitiveな確認ダイアログの基礎。
- PC調査画面からの確認付きアプリ起動。
- ローカル絶対`.exe`限定、UNC／device path／URL／相対パス／非対応拡張子拒否。
- file size、modified time、canonical pathの再検証。
- 一回限りのGrant消費、専用QThread Worker、`shell=False`、引数なしのPopen。
- Fake processによるProposal／Grant／Executor統合テスト。
- 実行・検証監査イベント型とInMemory／Null／JSONL Sinkの基礎。
- PyInstallerによるWindows実行ファイル生成。

Implementation baseline: `b7e283cd9168c33531ce49f17a194db1dff08b0a` (`fix: make confirmed launch executable and tested`).  
Validation at baseline: `102 passed`, compileall success, build success.

### Current limitations

- チャットからアプリ起動Toolへはまだ接続していない。
- AIへ公開されているToolは読み取り専用ファイル検索が中心。
- PowerShell実行runtimeは未実装。
- 本番の`LocalInspectionWindow`生成時に永続AuditSinkをまだ接続していない。
- `grant_consumed`／`grant_rejected`を含む実行監査の統合が未完成。
- UI／QThreadの製品フローを網羅する統合テストは不足している。
- Start Menuの`.lnk`リンク先を解決していない。
- Uninstall情報から実行ファイルを推定していない。
- PATHまたはApp Pathsへ登録されていないアプリは名前検索で見つからない場合がある。
- local persistent memory、revalidation、chat-server sharingは未実装。
- File server、Teams connector、UI automation、UAC brokerは未実装。

## 14. Roadmap

1. 本番AuditSink接続、Grant consume/reject監査、UI/QThread統合テスト。
2. チャットのアプリ起動要求を既存確認付きApplicationLaunchExecutorへ接続。
3. フルパス直接指定、Start Menu shortcut解決、installed appからの実行ファイル探索。
4. PowerShellExecutionProposal、Policy、確認画面、Executor、verification、auditの実装。
5. 低リスクPowerShell Tool: process、service、network、event log、winget、registry read。
6. 変更系PowerShell Tool: service、startup、registry write、scheduled task、Windows settings。
7. local persistent memory、revalidation、memory UI、API context compression。
8. File server、Teams connector。
9. chat-server shared memory、access control、provenance、conflict resolution。
10. UAC broker、rollback、update distribution、diagnostics、performance/cost optimization。

## 15. MVP acceptance criteria

- 「Xを起動して」で候補を発見または記憶から取得し、正確な対象を確認し、承認後に起動し、検証できる。
- PATH未登録アプリでもフルパス、shortcut、installed app情報から安全に候補化できる。
- 「このPCのサービス状態を確認して」でPowerShellを使った読み取り専用調査を実行できる。
- 変更系PowerShellはexact scriptを表示し、承認後に同一hashの内容だけを実行できる。
- 「このファイル名を変更して」でbefore/afterを表示し、承認、実行、検証できる。
- 「広告を止めて」で調査、計画、選択承認、実行、検証ができる。
- 必要なtoolがなければ、公式sourceを調査し、確認、install、verify、元タスク継続ができる。
- 再利用可能な発見をローカルへ保存し、安価な再検証後に再利用できる。
- すべての副作用にproposal、approval、execution、verificationの監査がある。
- 秘密情報がmodel input、通常ログ、共有記憶へ入らない。

## 16. AI decision rules

- peripheral UIより、実行agent、confirmation、verification、memoryを優先する。
- 実装指示作成前に最新sourceとHEADを確認し、本仕様との差分を特定する。
- 新しいToolにはside effect、confirmation、cancellation、verification、audit、memory updateを必ず定義する。
- Windows操作ではPowerShell利用を積極的に検討する。
- ただし、専用Executorの方が単純・安全・検証容易な場合は無理にPowerShellへ置き換えない。
- AIへ汎用の無確認shell権限を与えない。
- 自由形式PowerShellはexact script previewと強い確認を必須とする。
- chatやsearchが動作するだけで主要機能完成と判断しない。
- ユーザーの新要件は正式な仕様変更として扱い、変更箇所を明示する。
- Codex作業は原則Luna・軽。複数の安全境界や広範な統合で成功率が不足する場合のみ、理由を示してSol・軽を使用する。

## 17. Document governance

### Source of truth

```text
D:\work\WindowsPet\docs\WindowsPet_設計仕様書_v0.2.0.md
D:\work\WindowsPet\docs\WindowsPet_PowerShell実行設計_v0.1.0.md
```

Gitリポジトリ内のMarkdownを正本とする。設計変更はコード変更と同様にcommit履歴で管理する。

### ChatGPT Project shared storage

ChatGPTプロジェクト共有ストレージには、正本の参照用コピーを置く。目的は、新しいチャットや長期間後の会話でも、製品方針を確実に読み込めるようにすることである。

運用規則:

1. Git側を先に更新する。
2. commit後の最新版を共有ストレージへ差し替える。
3. 共有ストレージ上で別編集しない。
4. file名、version、date、baseline commitを一致させる。
5. 内容が競合した場合はGit側を正とする。

## 18. Revision history

- **0.1.0 — 2026-08-07:** Initial baseline covering product definition, confirmation, memory, shared backend, connectors, current state, and roadmap.
- **0.2.0 — 2026-08-07:** Updated current implementation state through confirmed local app launch; adopted PowerShell as a standard execution backend when appropriate; added exact-script confirmation, execution-backend selection, current limitations, revised roadmap, and document-governance rules.

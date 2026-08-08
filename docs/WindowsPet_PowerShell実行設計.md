# WindowsPet PowerShell実行設計

**Version:** 0.4.2\
**Date:** 2026-08-08\
**Audience:** ChatGPT / Codex / WindowsPet developers\
**Status:** Phase 3B state-change runtime and Phase 4B service-restart elevation wiring are implemented with Fake/read-only validation. Real UAC, real Broker Restart-Service, and real administrative operations are not executed by the development test path.\
**Related:** `WindowsPet_設計仕様書.md`

> **AIへの最重要指示:** PowerShellをWindowsPetの強力な実行バックエンドとして利用するが、AIへ無制限のshell権限を渡さない。AIは構造化された操作またはexact scriptを提案し、WindowsPetがPolicy Gate、確認、one-shot ExecutionGrant、実行前再検証、結果検証、監査を管理する。

## 0. Purpose

WindowsPetがWindowsの調査・管理・修復を迅速に実装できるよう、PowerShellを標準的なローカル実行バックエンドとして定義する。

対象例:

- process、service、event log、network、installed app、Windows featureの調査
- registry、startup、scheduled task、serviceの変更
- wingetによるinstall／upgrade／uninstall
- file、folder、ACL、shareの管理
- Windows設定の確認・変更
- 診断情報の収集と構造化

この設計は「何でも実行できるshell」を提供するものではない。実行内容を限定・可視化・束縛・検証するための設計である。

## 1. Design goals

1. PowerShellの広いWindows管理能力を利用する。
2. 専用Toolを大量に個別実装する前でも、実用的な操作範囲を広げる。
3. AIが提案した内容と実際の実行内容を一致させる。
4. 副作用は必ずPolicy Gateと確認を通す。
5. 読み取り専用操作と変更操作を明確に分離する。
6. 標準ユーザーを既定とし、管理者操作を別境界にする。
7. stdout、stderr、exit codeだけでなく、対象状態を検証する。
8. 秘密情報、個人情報、実行内容を必要以上にログへ残さない。
9. timeout、cancellation、output cap、process cleanupを決定論的に扱う。
10. Fake runnerによる自動テストで、実PowerShellを起動せずに大部分を検証できるようにする。

## 2. Non-goals

初期版では次を提供しない。

- 確認なしの任意PowerShell実行
- AIへの恒久的な管理者権限
- interactive shell／REPL
- ユーザーに見せていない追加コマンド
- `cmd.exe`を介した実行
- `shell=True`
- `-EncodedCommand`
- Base64や圧縮による実行内容の難読化
- remote scriptを取得してそのまま実行する処理
- PowerShell profileの読み込み
- unrestricted plugin/module auto-install
- 永続バックグラウンドagentやscheduled taskの無確認作成
- セキュリティ製品の無確認停止・除外追加
- credential dump、token dump、secret export

## 3. Execution modes

### 3.1 Structured operation mode — default

AIは操作名と構造化引数を返し、WindowsPetが管理されたPowerShell templateへ変換する。

例:

```json
{
  "operation": "get_service_status",
  "arguments": {
    "service_name": "Spooler"
  }
}
```

WindowsPet側のtemplate例:

```powershell
$ErrorActionPreference = 'Stop'
Get-Service -Name $ServiceName |
    Select-Object Name, DisplayName, Status, StartType |
    ConvertTo-Json -Compress
```

利点:

- input validationが容易
- script reviewが容易
- side effect classificationが決定論的
- verificationとtestを固定できる
- AIが不要な追加処理を混ぜにくい

### 3.2 Free-form reviewed script mode — advanced

定型Toolで対応できない場合、AIはPowerShell script全文を提案できる。

要件:

- script全文をActionProposalへ固定する
- script hashを計算する
- purpose、target、effect、requires_admin、timeout、rollback、verificationを含める
- 確認画面へscript全文を表示する
- 承認後に同じscript bytesだけを実行する
- 変更された場合はGrantを拒否し、再確認する
- 通常より強い確認形式を使用する

自由形式modeは便利さのために提供するが、structured operationへ置き換えられる場合はstructured modeを優先する。

## 4. Tool surface

AIへ公開するToolは、実行ではなく提案・計画の境界とする。

### 4.1 Structured PowerShell tool

```json
{
  "type": "function",
  "name": "request_powershell_operation",
  "description": "Windowsの調査または操作を、管理されたPowerShell操作として提案します。副作用はユーザー確認後にのみ実行されます。",
  "parameters": {
    "type": "object",
    "properties": {
      "operation": { "type": "string" },
      "arguments": { "type": "object" },
      "goal": { "type": "string" }
    },
    "required": ["operation", "arguments", "goal"],
    "additionalProperties": false
  },
  "strict": true
}
```

### 4.2 Free-form PowerShell proposal tool

```json
{
  "type": "function",
  "name": "propose_powershell_script",
  "description": "定型操作で対応できないWindows作業について、確認用PowerShellスクリプトを提案します。呼び出しだけでは実行されません。",
  "parameters": {
    "type": "object",
    "properties": {
      "purpose": { "type": "string" },
      "script": { "type": "string" },
      "effect_class": {
        "type": "string",
        "enum": ["read_only", "local_change", "system_change", "external_send"]
      },
      "requires_admin": { "type": "boolean" },
      "timeout_seconds": { "type": "integer", "minimum": 1, "maximum": 600 },
      "verification_plan": { "type": "string" },
      "rollback_plan": { "type": ["string", "null"] }
    },
    "required": ["purpose", "script", "effect_class", "requires_admin", "timeout_seconds", "verification_plan", "rollback_plan"],
    "additionalProperties": false
  },
  "strict": true
}
```

AIのTool callは実行権限ではない。WindowsPetがProposalを生成し、確認後にのみExecutorへ渡す。

## 5. PowerShellExecutionContract

```python
ToolContract(
    name="powershell_executor",
    version="1",
    operation="execute_powershell",
    side_effect=<classified per proposal>,
    confirmation=<NONE / SIMPLE / DIFF / PLAN_IMPACT / EXTERNAL_SEND / SCRIPT_REVIEW>,
    reversible=<bool>,
    requires_admin=<bool>,
    cancellation_support=True,
    timeout_seconds=<bounded>,
    verification_method=<operation-specific>,
    audit_fields=(
        "script_hash",
        "backend",
        "effect_class",
        "result_code",
        "verification_result",
    ),
)
```

Contractはoperationごとに固定値を持つ。AIがside effect、confirmation、requires_adminを自由に弱めることはできない。Policy側がoperation catalogから再計算する。

## 6. PowerShellExecutionProposal

推奨モデル:

```python
@dataclass(frozen=True)
class PowerShellExecutionProposal:
    operation_id: str
    purpose: str
    script_text: str
    script_sha256: str
    backend: str
    working_directory: str | None
    environment_keys: tuple[str, ...]
    timeout_seconds: int
    effect_class: str
    requires_admin: bool
    expected_changes: tuple[str, ...]
    verification_plan: str
    rollback_plan: str | None
```

### 6.1 Script canonicalization

hash対象は次で固定する。

1. UTF-8
2. BOMなし
3. 改行はLFへ正規化
4. 末尾改行を1つへ正規化
5. invisible control characterを拒否
6. null byteを拒否
7. script size上限を適用

hash:

```text
SHA-256(canonical_script_utf8)
```

ActionProposal fingerprintには次を含める。

- script_sha256
- backend
- working_directory
- environment allowlist
- timeout
- effect class
- requires_admin
- expected changes
- verification plan
- rollback plan

script本文は確認画面に表示するが、通常監査ログへは保存しない。

## 7. Static validation

PowerShell実行前に決定論的なvalidatorを通す。

### 7.1 Always reject

- `-EncodedCommand`
- `FromBase64String`など、実行目的の復号・難読化
- null byte、不可視制御文字
- script size上限超過
- nested `powershell.exe`／`pwsh.exe`で不透明なcommandを再実行
- `cmd.exe /c`を使った不透明な実行
- `Invoke-Expression`で外部または動的文字列を実行
- `irm ... | iex`、`iwr ... | iex`、download-and-execute
- 表示対象と異なる外部scriptの読み込み
- profile、registry、scheduled task等へ自己永続化する処理を、永続化operation以外で実行
- credential、token、browser secret、LSASSなどの抽出
- Windows Defender／EDR／firewallの無確認停止・除外追加

### 7.2 Require stronger review

次は通常のstructured modeより強い`SCRIPT_REVIEW`または`PLAN_IMPACT`を要求する。

- registry write
- service start/stop/restart/config change
- scheduled task create/update/delete
- startup change
- ACL、ownership、share permission change
- winget install/uninstall/upgrade
- reboot、shutdown、logoff
- process termination
- network configuration change
- firewall rule change
- module installation
- remote connection
- external upload/send
- bulk file operation

### 7.3 Read-only classification

読み取り専用と判断できるoperationでも、scriptを文字列だけで安全判定しない。operation catalogの定義、template ID、許可cmdlet、引数schemaで分類する。

## 8. Backend discovery and selection

優先順位:

1. `pwsh.exe`（PowerShell 7）
2. `powershell.exe`（Windows PowerShell 5.1）

Discovery:

- `shutil.which("pwsh.exe")`
- App Paths
- known safe system location
- `shutil.which("powershell.exe")`

取得したpathはcanonicalizeし、ローカル絶対`.exe`、file identity、publisher/signature確認を可能な範囲で行う。記憶する場合はcheap revalidationを行う。

operation catalogは必要なPowerShell versionを定義する。

```text
minimum_version
preferred_backend
requires_windows_powershell
requires_pwsh
required_modules
```

## 9. Process invocation

### 9.1 General requirements

- `subprocess.Popen`または`subprocess.run`
- `shell=False`
- argvを配列で渡す
- profileを読み込まない
- non-interactive
- timeoutを必須化
- stdout/stderrをcapture
- input/output size上限
- text encodingを明示
- working directoryを固定
- environmentはallowlist方式

### 9.2 Preferred invocation

secured temporary `.ps1` fileを使用する方式を推奨する。

```python
subprocess.Popen(
    [
        powershell_executable,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        temporary_script_path,
    ],
    shell=False,
    cwd=working_directory,
    env=restricted_environment,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    creationflags=CREATE_NO_WINDOW,
)
```

Windows PowerShellでexecution policyにより必要な場合のみ、process scopeの`-ExecutionPolicy Bypass`を追加できる。ただし、次を満たすこと。

- machine/user policyを変更しない
- invocation previewへ明示する
- ActionProposal fingerprintへ含める
- script hashを再検証する
- temporary fileをアクセス制御する
- 実行後に安全に削除する

`-Command`で文字列を直接渡す方式は、quoting差異と表示内容不一致を避けるため、定型の短いread-only operation以外では使用しない。

### 9.3 Temporary script handling

- user専用local temp directory
- ランダムfile name
- `.ps1`
- 書き込み後にSHA-256再計算
- Grantのscript hashと一致しなければ実行拒否
- 実行直前にfile identity再確認
- secretをscript本文へ埋め込まない
- 完了・失敗・cancel後に削除
- 削除失敗は監査し、内容はログへ出さない
- crash recoveryで古いtemp scriptを清掃

## 10. Environment and secrets

### 10.1 Environment allowlist

既定で継承する候補:

- `SystemRoot`
- `WINDIR`
- `COMSPEC`（直接使用は禁止だが一部OS処理互換性のため）
- `TEMP`
- `TMP`
- `USERPROFILE`
- `ProgramFiles`
- `ProgramFiles(x86)`
- operationに必要な限定値

不要なapplication-specific secret環境変数は継承しない。

### 10.2 Secret handling

- API key、password、tokenをscript literalへ埋め込まない。
- OS credential storageからExecutor内で取得する。
- stdin、named pipe、protected environment injectionなど、用途別の安全な受け渡しを使用する。
- 確認画面、script preview、stdout/stderr、auditへ秘密値を表示しない。
- redactionは実行後だけでなく、実行前のProposal生成時にも行う。

## 11. Output contract

### 11.1 Structured output

read-only Toolは可能な限りJSONを返す。

PowerShell側:

```powershell
$result = [ordered]@{
    status = 'ok'
    items  = @(...)
}
$result | ConvertTo-Json -Depth 6 -Compress
```

WindowsPet側:

- UTF-8 decode
- JSON parse
- schema validation
- item count／string length／nesting depth上限
- secret／path／personal data filtering
- AIへ渡す情報を最小化

### 11.2 Raw output

raw stdout/stderrが必要な場合:

- byte／character上限
- line count上限
- truncation marker
- control character除去
- ANSI escape除去
- secret redaction
- user displayとAI inputを分離

### 11.3 Result model

```python
class PowerShellExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"
    PARTIAL = "partial"

@dataclass(frozen=True)
class PowerShellExecutionOutcome:
    status: PowerShellExecutionStatus
    result_code: str
    exit_code: int | None
    verification_result: str
    safe_output: object | None
```

Outcomeへscript本文、secret、実パス、例外本文を含めない。

## 12. Timeout and cancellation

### 12.1 Before process creation

- cancellation可能
- Grant未消費ならcancelできる
- Grant消費後でもprocess作成前なら実行しない
- 消費済みGrantは再利用不可

### 12.2 During execution

- cancellation要求をPowerShell runnerへ通知
- cooperative cancellationが可能なtemplateでは停止フラグを確認
- timeout／cancel時はPowerShell processを終了できる
- child processを生成するoperationは、child ownershipとcleanup ruleをContractへ定義する
- 外部アプリ起動のように、起動後の対象を自動終了してはいけないoperationは専用Executorを使う

### 12.3 Process tree

PowerShell自体を停止する必要があるoperationでは、Windows Job Object等でrunner process treeを管理する。目的外の既存processを巻き込まない。

## 13. Privilege and UAC

標準ユーザーを既定とするが、WindowsPetのローカルPC問題解決能力に管理者操作を含める。管理者権限が必要な場合は、専用Elevation Brokerを使用し、UACとone-shot Grantを通した単一操作として実行する。Elevation Brokerは変更系PowerShell runtimeと同じ基盤フェーズで実装対象とし、「将来の別機能」として後回しにしない。

```text
Main WindowsPet
  -> approved elevated proposal
  -> signed/local elevation helper
  -> UAC prompt
  -> exact proposal/hash validation
  -> one operation
  -> result/verification
  -> helper exit
```

禁止:

- 任意scriptから`Start-Process -Verb RunAs`を直接呼ぶ
- 長時間常駐する管理者helper
- Grantを複数操作へ再利用
- UAC後にscriptを差し替える

## 14. Confirmation UI

### 14.1 Read-only structured operation

原則確認不要。ただし、秘密・個人情報・大規模取得・外部送信が関係する場合は確認する。

### 14.2 Simple state change

表示:

- 目的
- 対象
- 操作
- 影響
- requires admin
- timeout
- verification
- rollback可否

### 14.3 Script review

表示:

- purpose
- exact script全文
- script SHA-256短縮表示
- backend
- working directory
- environment概要
- target／expected changes
- requires admin
- timeout
- rollback plan
- verification plan

UI要件:

- Cancelがdefault、初期focus
- Enterだけで実行しない
- Executeは明示click
- EscapeはCancel
- closeはClosed
- expiryはExpired
- copy buttonを提供
- syntax highlightは可能だが、表示文字列を変更しない
- scriptを省略・折りたたむ場合も、実行前に全文閲覧可能にする
- 「今後は確認しない」は提供しない

## 15. Policy classification

operation catalogの例:

| Operation | Default side effect | Confirmation | Admin | Verification |
|---|---|---|---|---|
| get_processes | READ_ONLY | NONE | No | schema/content |
| get_services | READ_ONLY | NONE | No | schema/content |
| get_event_logs | READ_ONLY | NONE | Sometimes | schema/count |
| get_network_config | READ_ONLY | NONE | No | schema/content |
| stop_process | PROCESS_TERMINATION | SIMPLE | Sometimes | process absent |
| restart_service | SYSTEM_CHANGE | PLAN_IMPACT | Often | service running |
| set_registry_value | SYSTEM_CHANGE | DIFF | Sometimes | value reread |
| create_scheduled_task | SYSTEM_CHANGE | PLAN_IMPACT | Often | task exists/config |
| winget_install | INSTALLATION | INSTALL | Often | package/version |
| winget_uninstall | UNINSTALLATION | INSTALL | Often | package absent |
| modify_firewall_rule | SYSTEM_CHANGE | PLAN_IMPACT | Yes | rule reread |
| send_external_data | EXTERNAL_SEND | EXTERNAL_SEND | No | delivery result |

AIが申告したeffect classではなく、catalogの定義を正とする。

## 16. Verification

実行成功はexit code 0だけで判断しない。

### Examples

- service restart: `Get-Service`でStatusを再取得
- registry write: 対象valueを再読込
- winget install: package listとversionを確認
- scheduled task: task definitionとstateを確認
- network change: adapter／route／DNSを再取得
- file operation: existence、hash、size、mtimeを確認

verificationもread-only Toolとして独立させ、実行scriptと異なる経路で確認できる設計を優先する。

## 17. Audit

イベント例:

```text
powershell_proposal_created
powershell_policy_classified
powershell_confirmation_shown
powershell_confirmation_approved
powershell_confirmation_cancelled
powershell_grant_issued
powershell_execution_started
powershell_execution_succeeded
powershell_execution_failed
powershell_execution_cancelled
powershell_execution_timed_out
powershell_verification_succeeded
powershell_verification_failed
powershell_temp_cleanup_failed
```

通常監査へ保存:

- task ID
- proposal ID／fingerprint
- confirmation session ID
- grant ID
- operation ID
- template ID／version
- script SHA-256
- backend name／version
- effect class
- requires admin
- timeout
- result code
- exit code
- verification result
- timestamps／duration

通常監査へ保存しない:

- script本文
- raw stdout／stderr
- secret
- personal data
- exact personal path
- exception本文
- access token／credential

詳細診断を保存する場合は、明示設定、暗号化、access control、retentionを別途設ける。

## 18. Memory

保存可能:

- operation template ID／version
- successful procedure
- required modules／minimum version
- backend preference
- verification method
- common error codeと対処
- device-independent variable template

保存しない:

- raw script内の端末固有path
- secret
- one-time values
- personal output

再利用時:

- backend availability
- PowerShell version
- required module
- target existence
- permission
- template version
- policy version

を再検証する。

## 19. Testing strategy

### 19.1 Unit tests

- canonicalization
- hash stability
- invisible character rejection
- size limit
- static policy rejection
- effect classification
- Contract generation
- confirmation preview
- Grant binding
- timeout／cancel state transition
- output cap／redaction／JSON schema
- audit field allowlist

### 19.2 Executor tests

Fake processで検証:

- exact argv
- `shell=False`
- `-NoProfile`
- `-NonInteractive`
- exact script file hash
- working directory
- environment allowlist
- stdout／stderr capture
- exit 0／nonzero
- timeout
- cancel
- cleanup
- hash mismatchでprocess未起動
- expired／wrong／reused Grantでprocess未起動

### 19.3 Integration tests

実PowerShellを使わないFake統合:

```text
AI Tool call
→ dispatcher
→ operation catalog
→ ActionProposal
→ confirmation response
→ ExecutionGrant
→ PowerShellWorker
→ Fake process
→ verification
→ audit
```

### 19.4 Controlled Windows tests

自動テストとは分離し、専用sandbox／test accountで実施する。

- read-only operation
- temporary registry test key
- temporary file/folder
- temporary scheduled task
- test service where available
- winget source query without install

本番端末での破壊的testは禁止する。

## 20. Initial implementation phases

### Phase 1 — Read-only runner

- backend discovery
- operation catalog
- structured Proposal
- read-only Policy
- exact argv
- timeout/cancel
- safe output parser
- audit
- Fake tests

Initial operations:

```text
get_powershell_version
get_processes
get_services
get_network_configuration
get_installed_applications
get_event_log_summary
get_disk_usage
```

### Phase 2 — Chat connection

- AI Tool schema
- dispatcher
- read-only operations from chat
- safe output summarization
- no state changes

### Phase 3 — Confirmed state changes

- exact script review
- script hash binding
- one-shot Grant
- file／process／service／registry operations
- verification
- rollback metadata

### Phase 4 — Administration and one-shot elevation

- Elevation Broker / UAC
- install／upgrade／uninstall
- scheduled task
- startup
- firewall
- Windows settings
- network configuration
- ACL / ownership
- exact proposal/hash validation across elevation boundary

Phase 4A foundation contract:

- Main creates an immutable `ElevationRequest` only after the existing `ConfirmationSession` and `ExecutionGrant` approval.
- `ElevationEnvelope` is UTF-8 canonical JSON with sorted keys, compact separators, strict required/unknown-field validation, bounded size/depth, expiry, nonce, parameter digest, and no script body or secret.
- The envelope is written to a random file under the current user's local application-data elevation directory with exclusive creation, bounded size, post-write SHA-256, and reparse-point/path-boundary checks.
- The Broker accepts only the local expected helper identity and the `restart_service` operation. It reconstructs the local template, validates canonical service parameters, compares the exact script SHA-256, requires `system_change` and `requires_admin == true`, claims the grant and nonce atomically, then dispatches exactly one operation and exits.
- Cross-process replay protection is provided by exclusive claim files under `AppLocalData\WindowsPet\elevation\claims`; only hash identifiers are written. A second Broker process receives `grant_reused` or `replayed_nonce` and its execution count is zero.
- Main validates `request_id`, `operation_id`, and script hash in the structured result and requires an independent read-only verifier before reporting success. Broker exit code alone is insufficient.
- `FakeElevationLauncher`／`FakeElevatedExecutor` cover approval, rejection, replay, UAC-cancel simulation, nonzero exit, verification failure, and process-race tests. `WindowsElevationLauncher` is a native `ShellExecuteExW`/`runas` skeleton; it is not invoked by automated tests.
- The dedicated `WindowsPet.ElevationBroker.exe` PyInstaller spec is separate from the GUI executable. The Broker has no AI, API, Web, network IPC, TCP listener, `shell=True`, `cmd.exe /c`, `-EncodedCommand`, or PowerShell `Start-Process -Verb RunAs` path.

Phase 4A acceptance status: Fake and read-only validation complete; real UAC prompt, real elevation, and real `Restart-Service` remain pending by design. Phase 3B's latest-build real `Restart-Service` confirmation is also pending.

Phase 4B production wiring:

- `ServiceResolutionCode.ADMIN_REQUIRED` now proceeds through the existing SCRIPT_REVIEW confirmation instead of being rejected before confirmation. The already-admin path retains `ServiceRestartRunner`; the standard-user path consumes the Main-side grant exactly once before creating `ElevationRequest`.
- The standard-user path uses the existing `ElevationQtController` and `WindowsElevationLauncher`, resolves only the bundled `WindowsPet.ElevationBroker.exe` from the trusted application directory, and fails closed before UAC when the helper is missing or has the wrong identity.
- The Broker's normal constructor remains Fake-safe. `BrokerEntryPoint.production()` explicitly installs `ElevatedRestartServiceExecutor`, whose only operation is the canonical `restart_service` template with fixed PowerShell arguments, restricted environment, hash checks, bounded execution, cooperative cancellation, and temporary-script cleanup.
- The Broker derives `envelope-<random>.result.json` from the validated envelope path. The CLI has no free `--result` path; result schema, canonical JSON, request binding, operation binding, script-hash binding, size bound, exclusive creation, and root boundary are enforced.
- Main performs independent read-only verification of the canonical service name, display identity, and actual `Running` state. Broker exit code alone never produces a success message.
- Automated Phase 4B validation covers standard-user normal/cancel/fail-closed flows, one-shot grant reuse, wrong result bindings, production executor Fake Popen boundaries, and integrated Qt stress (normal 100, cancel 50, shutdown 50). The final regression run is 286 passed, skipped 0, xfailed 0.

Phase 4B acceptance status: automated Fake/read-only validation and both PyInstaller targets are complete. Real UAC prompt, real elevation, real Broker `Restart-Service`, signed-helper validation, and Phase 3B's latest-build direct real `Restart-Service` confirmation remain pending by design.

Low-risk event-log read:

- `inspect_windows` now accepts `event_logs` with an optional explicit log name; omitted log name deterministically selects `System`.
- The generated script uses only `Get-WinEvent -LogName ... -MaxEvents ...`, with no mutating cmdlet, arbitrary script input, or shell boundary change.
- Results are strict `schemaVersion=1` records containing bounded log name, event ID, level, provider, timestamp, and a message capped at 2048 characters. Fake process output and invalid-shape rejection are covered by tests.

Low-risk registry read:

- `inspect_windows` accepts `registry` only with the code-owned catalogs `app_paths` and `installed_apps`; omitted query selects `app_paths`.
- The generated script enumerates only the fixed HKCU/HKLM application metadata roots and reads `DisplayName` values. Arbitrary registry paths, sensitive-key discovery, writes, and startup changes are outside this capability.
- Results are bounded strict records containing the catalog, canonicalized path text, value name, and a value capped at 512 characters. Fake output and unsupported-catalog rejection are tested.

### Phase 5 — Memory and shared procedures

- local procedure memory
- revalidation
- safe template sharing
- conflict handling

## 21. Acceptance criteria

- AIからPowerShell操作を依頼しても、Tool callだけでは実行されない。
- read-only structured operationは、許可catalogとschema内で実行できる。
- state changeはexact preview、confirmation、Grantを通る。
- 承認したscript hashと実行script hashが一致する。
- hash不一致、expired Grant、reused Grantではprocessを起動しない。
- `shell=False`、`-NoProfile`、`-NonInteractive`を使用する。
- timeoutとcancelが有界時間で終了する。
- secretがscript、preview、audit、AI inputへ漏れない。
- exit codeと対象状態の両方を検証する。
- 通常監査へscript本文とraw outputを保存しない。
- Fake testで実PowerShellを起動せずに主要経路を検証できる。
- Elevation Envelopeの同一grant／nonceを別Brokerプロセスへ渡しても一回だけclaimされ、競合時もexecution countが一回以下である。
- Broker resultのrequest／operation／script hashをMain側で照合し、独立read-only verificationが成功するまで成功表示しない。

## 22. Document governance

Canonical file: `docs/WindowsPet_PowerShell実行設計.md`. Keep the filename stable; use the document `Version` field and Git history for revisions. The general product identity, Local-first AI model, cloud-model routing and shared-knowledge rules are defined by `WindowsPet_設計仕様書.md` and `WindowsPet_AI_Memory_Learning_Spec.md`; this document remains focused on safe PowerShell execution.

## 23. Revision history

- **0.1.0 — 2026-08-07:** Initial PowerShell execution design. Defines structured and reviewed-script modes, exact-script hash binding, Policy/Confirmation/Grant flow, backend selection, safe invocation, secrets, output, cancellation, privilege, verification, audit, memory, testing, and phased implementation.
- **0.2.0 — 2026-08-08:** Promoted one-shot UAC elevation from a future concept to a core administration capability implemented alongside state-changing PowerShell, supporting the Local PC full-capability principle without granting persistent administrator rights.
- **0.2.1 — 2026-08-08:** Adopted stable Git-canonical filenames and aligned references with the Local-first WindowsPet architecture without changing PowerShell safety boundaries.
- **0.3.0 — 2026-08-08:** Implemented the Phase 4A one-shot Elevation Broker foundation: canonical Envelope, secured payload file, exact catalog/hash validation, cross-process file-backed grant/nonce claims, Fake/native launcher boundary, structured result binding, independent verification hook, Qt lifecycle, and dedicated Broker build target. Real UAC and real administrative operations remain unexecuted.
- **0.4.0 — 2026-08-08:** Connected the Phase 4A contract to the service-restart UI for both admin-direct and standard-user elevation paths; added Main Grant consume, fixed production Broker executor, bounded deterministic result files, read-only verification, and Fake/read-only integrated stress. Real UAC and real administrative operations remain unexecuted.
- **0.4.1 — 2026-08-08:** Added the low-risk `event_logs` inspection area with fixed `Get-WinEvent` generation, bounded strict result validation, AI tool schema exposure, and Fake/read-only tests. No state-changing event-log capability was added.
- **0.4.2 — 2026-08-08:** Added the fixed-catalog `registry` inspection area for application metadata, with strict catalog validation, bounded result schema, and Fake/read-only tests. Arbitrary registry access and writes remain excluded.

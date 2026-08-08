# WindowsPet キャラクター・アニメーション仕様

**Version:** 0.1.2\
**Date:** 2026-08-08\
**Audience:** ChatGPT / Codex / WindowsPet developers\
**Status:** Design baseline; character replacement and animation editor are not yet implemented.\
**Related:** `WindowsPet_設計仕様書.md`

> **AIへの最重要指示:** キャラクターはWindowsPet本体の実行エージェント、確認、検証、記憶、監査から分離された表示層として扱う。キャラクターパッケージから任意コード、PowerShell、外部コマンド、PC操作を実行してはならない。キャラクター差し替えによってWindowsPetの権限境界や安全設計を変更しないこと。

## 0. Quick reference

- WindowsPetのキャラクターは、将来的にユーザーが差し替え可能にする。
- キャラクターはWindowsPetという一貫した知性主体の表現層であり、Local AI／Luna／Terra／Solの切替を別人格として表現しない。
- 外部AIやWeb調査の待ち時間は、`thinking`／`researching`／`planning`等の自然な状態アニメーションとして表現できる。
- キャラクターの画像、アニメーション、イベント割り当ては、本体のエージェントロジックから分離する。
- 現在使用している `idle`、`sleep`、`thinking`、`wave` を初期の必須アニメーションとする。
- 必須以外のイベントアニメーションは任意で追加できる。
- 任意イベントの例は、ダブルクリック、長時間ホバー、シングルクリック、右クリック、ドラッグ開始・終了など。
- 設定UIは、イベントを縦方向に並べ、各イベントのフレーム画像を横方向に並べる。
- 1イベントは2〜10コマで構成する。
- 各コマは画像と表示時間 `durationMs` を保持する。
- コマの再生順は、同一イベント行内のドラッグ＆ドロップで変更できる。
- 画像は、行末の「＋」ボタンからファイル選択ダイアログで追加するか、Windowsエクスプローラーから直接ドラッグ＆ドロップして追加する。
- キャラクターパッケージは画像と宣言的な設定だけを含み、実行可能コードを含めない。

## 1. Purpose

本仕様は、WindowsPetのキャラクターを差し替え可能にし、キャラクターごとに必須アニメーションと任意イベントアニメーションを設定できる仕組みを定義する。

目的:

1. WindowsPet本体の機能を変更せずに、見た目と反応を差し替えられるようにする。
2. 最低限の素材だけでキャラクターを作成できるようにする。
3. 作り込んだキャラクターは、クリックやホバーなど多数の反応を追加できるようにする。
4. 画像追加、表示時間設定、並べ替えを、専門知識なしで操作できるUIにする。
5. キャラクターパッケージを導入しても、PC操作や権限境界へ影響しないようにする。

## 2. Scope

### 2.1 Initial scope

初期版で対象とするもの:

- キャラクターの選択・差し替え
- 必須アニメーションの設定
- 任意イベントアニメーションの追加・削除
- 1イベントあたり2〜10コマの画像設定
- コマごとの表示時間設定
- 横並びフレームのドラッグ＆ドロップ並べ替え
- ファイル選択ダイアログからの画像追加
- エクスプローラーからの画像ドラッグ＆ドロップ追加
- アニメーションのプレビュー
- キャラクターパッケージの検証と安全な読み込み

### 2.2 Non-goals for the initial version

初期版では次を対象外とする。

- キャラクターパッケージ内のPython、PowerShell、JavaScriptなどの実行
- キャラクター固有のPC操作、アプリ起動、外部送信
- キャラクターごとの独自Toolやプラグイン
- 3Dモデル、Live2D、動画ファイル
- オンラインストアや自動ダウンロード
- キャラクター制作者による任意のイベントコード登録
- キャラクターからWindowsPetの確認、Policy Gate、ExecutionGrantを迂回する処理

## 3. Separation of responsibilities

```text
WindowsPet本体
  ├─ Agent Orchestrator
  ├─ Policy / Confirmation / ExecutionGrant
  ├─ Local Tool Runtime
  ├─ Verification / Memory / Audit
  ├─ Runtime Event Dispatcher
  └─ Character Animation Player

キャラクターパッケージ
  ├─ manifest.json
  ├─ character image assets
  ├─ animation definitions
  └─ presentation metadata
```

WindowsPet本体がイベントを発行し、Character Animation Playerが対応するアニメーションを再生する。キャラクターパッケージは、イベント発生そのものやWindows操作を実行しない。

## 4. Character package

### 4.1 Recommended folder structure

```text
characters/
└─ default_pet/
   ├─ manifest.json
   ├─ thumbnail.png
   ├─ icon.png
   └─ animations/
      ├─ idle/
      ├─ sleep/
      ├─ thinking/
      ├─ wave/
      ├─ double_click/
      └─ hover_long/
```

実際の再生順はファイル名順ではなく、`manifest.json`のフレーム配列順を正とする。

### 4.2 Allowed content

許可するファイル:

- PNG
- WebP
- 将来明示的に許可した静止画像形式
- JSON
- thumbnail、icon
- ライセンス・説明用テキスト

初期対応画像形式はPNGを必須とし、WebP対応は実装時にQt/PyInstaller環境で検証して採用する。

### 4.3 Prohibited content

キャラクターパッケージから次を読み込み・実行しない。

- `.py`
- `.ps1`
- `.bat`
- `.cmd`
- `.exe`
- `.dll`
- `.js`
- マクロ
- 外部URLから取得する実行内容
- 任意コードを埋め込める設定値

未知のファイルは無視またはパッケージ検証エラーとし、実行対象にしない。

## 5. Event model

### 5.1 Required events

初期の必須アニメーションは、現在のWindowsPetで使用している次の4種類とする。

| Event ID | 用途 | 推奨再生 |
|---|---|---|
| `idle` | 通常待機 | loop |
| `sleep` | 睡眠・長時間待機 | loop |
| `thinking` | AI応答待ち・処理検討中 | loop |
| `wave` | 挨拶・簡単な反応 | one-shot または loop |

必須イベントが不足しているキャラクターパッケージは、保存・有効化前にエラーを表示する。

互換性確保のため、読み込み時に異常が判明した場合は、WindowsPet本体を停止させず、既定キャラクターへ戻す。

### 5.2 Optional events

必須以外のイベントは任意とし、キャラクターごとに設定の有無を選択できる。

初期候補:

#### Mouse events

- `single_click`
- `double_click`
- `right_click`
- `hover_long`
- `drag_start`
- `drag_end`

#### Agent state events

- `listening`
- `speaking`
- `researching`
- `planning`
- `working`
- `waiting_confirmation`
- `success`
- `error`
- `cancelled`
- `notification`

#### Time and idle events

- `startup`
- `return_from_sleep`
- `inactive_long`
- `morning`
- `night`

任意イベントの正式な初期登録範囲は実装タスク開始時に確定する。未設定イベントが発生した場合は、現在の高優先度アニメーションを維持するか、`idle`へ戻す。

### 5.3 Long hover

`hover_long`は、カーソルが一定時間キャラクター上に留まった場合に発生する。

推奨初期値:

```json
{
  "thresholdMs": 2000,
  "cooldownMs": 30000,
  "oncePerHover": true
}
```

- `thresholdMs`: 発火までの滞在時間
- `cooldownMs`: 再発火を禁止する時間
- `oncePerHover`: カーソルを一度外すまで1回だけ発火

これらの値をキャラクターパッケージ側で自由設定させるか、WindowsPet本体のユーザー設定とするかは、実装前に確定する。安全性と一貫性の観点では、本体設定を優先する。

## 6. Animation and frame model

### 6.1 Frame count

- 1イベントあたり最低2コマ
- 1イベントあたり最大10コマ
- データ構造は可変長配列
- 初期UIとvalidatorが2〜10コマを強制する
- 将来上限を変更しても、manifest形式を変更しなくてよい構造にする

1コマだけの静止画像は初期仕様ではアニメーションとして認めない。静止表示が必要な場合は、同一画像を2コマ登録する運用ではなく、将来の静止状態仕様として別途検討する。

### 6.2 Frame data

各コマは、画像と表示時間を一体のデータとして保持する。

```json
{
  "id": "frame_03",
  "file": "animations/double_click/frame_03.png",
  "durationMs": 300
}
```

必須フィールド:

- `id`: イベント内で一意な安定ID
- `file`: パッケージルートからの相対パス
- `durationMs`: このコマの表示時間

ドラッグ＆ドロップで再生順を変更した場合、画像、`id`、`durationMs`を一体として移動する。

### 6.3 Duration

各コマの表示時間はミリ秒単位で設定する。

初期仕様:

- 既定値: `150ms`
- 最小値: `50ms`
- 最大値: `5000ms`
- UIの標準増減単位: `50ms`
- 整数のみ

範囲外、数値以外、未入力は保存不可とし、該当フレームを強調表示する。

### 6.4 Animation data example

```json
{
  "id": "double_click",
  "required": false,
  "playback": "once",
  "frames": [
    {
      "id": "frame_a",
      "file": "animations/double_click/a.png",
      "durationMs": 100
    },
    {
      "id": "frame_b",
      "file": "animations/double_click/b.png",
      "durationMs": 500
    }
  ]
}
```

### 6.5 Playback modes

初期実装では、少なくとも次を使用する。

- `loop`: 繰り返し
- `once`: 1回再生後に以前の状態または`idle`へ戻る

`ping_pong`、ランダム再生、複数パターンの重み付き選択は将来拡張候補とし、初期必須要件には含めない。

## 7. Animation editor UI

### 7.1 Overall layout

イベントを縦方向に一覧表示し、各イベントの画像フレームを横方向に並べる。

```text
┌────────────────────────────────────────────────────────────────────┐
│ キャラクターアニメーション設定                                   │
├───────────────┬────────────────────────────────────────────────────┤
│ idle    必須  │ [1] [2] [3] [4] [+]                               │
│               │150 150 300 150 ms                                  │
├───────────────┼────────────────────────────────────────────────────┤
│ sleep   必須  │ [1] [2] [3] [4] [+]                               │
│               │300 300 500 300 ms                                  │
├───────────────┼────────────────────────────────────────────────────┤
│ thinking 必須 │ [1] [2] [3] [+]                                   │
│               │100 100 200 ms                                      │
├───────────────┼────────────────────────────────────────────────────┤
│ double_click  │ [1] [2] [+]                                       │
│        任意   │100 500 ms                                          │
├───────────────┼────────────────────────────────────────────────────┤
│ hover_long    │ 未設定                              [設定する]      │
└───────────────┴────────────────────────────────────────────────────┘
```

### 7.2 Event row

各イベント行は次を持つ。

左側:

- 表示名
- Event ID
- 必須／任意表示
- 有効／無効
- プレビューボタン
- 必要に応じて再生方式

右側:

- 横並びのフレームカード
- 行末の「＋」追加カード
- コマ数表示（例: `4 / 10`）
- 10コマを超える場合に備えた横スクロール領域

### 7.3 Frame card

各フレームカードは次を持つ。

- 再生順番号
- サムネイル
- 表示時間入力（ms）
- ドラッグ用ハンドル
- 画像差し替え
- 削除

```text
┌──────────┐
│ ⋮⋮    3  │
│          │
│ 画像     │
│          │
├──────────┤
│ 300 ms   │
│ 交換  削除│
└──────────┘
```

画像をクリックした場合は、拡大プレビューまたは画像差し替え操作を提供する。どちらを既定動作にするかは、実装時にUI一貫性を確認して決定する。

### 7.4 Event grouping

イベント数の増加に備え、カテゴリ単位で折りたたみ可能にする。

```text
▼ 必須イベント
  idle
  sleep
  thinking
  wave

▶ マウスイベント
▶ エージェント状態
▶ 時間・放置イベント
```

必須イベントカテゴリは初期表示で展開する。任意カテゴリの初期展開状態はユーザーの前回状態を保存してよい。

## 8. Adding images

### 8.1 Plus button

各イベント行の末尾に「＋」ボタンまたは追加カードを配置する。

操作:

1. 「＋」を押す。
2. Windowsのファイル選択ダイアログを開く。
3. 対応画像を選択する。
4. イベントの末尾へコマとして追加する。
5. `durationMs`へ既定値`150`を設定する。
6. UIへ即時反映し、プレビュー可能にする。

ファイル選択ダイアログでは、対応画像形式をフィルター表示する。

複数画像の同時選択は実装推奨とする。採用する場合、選択された順序をOSから安定して取得できない可能性があるため、追加前に順序確認を表示するか、ファイル名の自然順で追加したことを明示する。

### 8.2 Drag and drop from Windows Explorer

Windowsエクスプローラーから画像ファイルを、対象イベント行へ直接ドラッグ＆ドロップできるようにする。

ドロップ先:

- 行末または「＋」カード: 末尾へ追加
- フレーム間: その位置へ挿入
- イベント行の空白領域: 末尾へ追加

ドラッグ中は次を表示する。

- 追加対象イベント行の強調
- 挿入位置インジケーター
- 追加可能／追加不可カーソル
- 上限到達時の拒否表示

複数ファイルをドロップした場合は、一括追加できるようにする。追加順は、ドラッグデータの順序が利用可能ならその順序を使用し、不安定な場合はファイル名の自然順を使用してUI上で明示する。

### 8.3 Validation on add

画像追加時に検証する。

- 対応拡張子
- 実際に画像としてデコード可能か
- ファイルサイズ上限
- 画像寸法上限
- パスがローカルファイルか
- 最大10コマを超えないか
- 重複追加の扱い

重複画像は初期版では許可してよい。ただし、同一ファイルを追加しようとした場合は、誤操作の可能性を示す軽い警告を検討する。

### 8.4 Asset handling

外部画像を設定した場合、元ファイルへの絶対パスを恒久参照しない。キャラクターパッケージの管理領域へコピーし、manifestにはパッケージ内相対パスを保存する。

コピー前に必要な容量を確認し、失敗時はmanifestを変更しない。保存処理は一時領域へ書き込み、検証後に置き換える方式を推奨する。

## 9. Reordering frames

### 9.1 In-row drag and drop

既存フレームカードは、同一イベント行内で左右へドラッグして再生順を変更できる。

```text
[1] [2] [3] [4]
        ↓ 3を先頭へ移動
[3] [1] [2] [4]
```

再生順はmanifestの`frames`配列順として保存する。画像ファイル名は並べ替えに合わせて変更しない。

### 9.2 Drag source separation

次の操作を明確に区別する。

- エクスプローラーからの外部ファイルドロップ: 新規コマ追加
- 既存カードのドラッグハンドルからの内部ドラッグ: 並べ替え

フレームカード全体を並べ替え開始領域にすると、表示時間入力や画像クリックと競合するため、専用ドラッグハンドルを使用する。

### 9.3 Cross-event movement

初期版では、イベント行をまたぐ既存フレーム移動を禁止する。誤設定を防ぎ、コピーと移動の意味を曖昧にしないためである。

将来対応する場合は、明示的なコピー操作として設計し、元イベントから自動削除しない。

## 10. Frame count rules

### 10.1 Minimum

2コマの状態で削除操作を行う場合は、削除を無効化するか、最低2コマ必要であることを表示する。

任意イベントを完全に削除する場合は、イベントのアニメーション設定自体を削除する操作を別途提供する。

### 10.2 Maximum

10コマに達したイベントでは次の状態にする。

- 「＋」ボタンを無効化
- 外部ファイルドロップを拒否
- `10 / 10`を表示
- 「このイベントには最大10コマまで設定できます」と表示

複数画像追加で上限を超える場合、無言で一部だけ追加しない。次のいずれかを選ぶ。

- 全件を拒否し、追加可能枚数を表示する
- 追加対象を選択する確認UIを表示する

初期版では全件拒否を推奨する。挙動が決定論的で、ユーザーが追加された画像を誤認しにくいためである。

## 11. Preview

各イベント行にプレビューボタンを設ける。

プレビュー要件:

- manifest上の現在の順序を使用する
- 各フレームの`durationMs`を反映する
- 変更を保存する前でも確認できる
- プレビュー中に再度押すと先頭から再生する
- 停止操作を提供する
- 実際のキャラクター表示サイズまたは近い表示サイズで確認できる
- 透過画像を正しく表示する

プレビューは表示のみであり、WindowsPetの実際のエージェント状態やPC操作を発生させない。

## 12. Runtime event priority

任意のクリック・ホバー反応が、本来の状態表示を妨げないように優先順位を設ける。

推奨優先順位:

```text
error / waiting_confirmation
  > researching / planning / working / thinking
  > speaking / notification
  > mouse interaction events
  > idle / sleep
```

例:

- `thinking`中に長時間ホバーしても`hover_long`で中断しない。
- `idle`中のダブルクリックは`double_click`を再生できる。
- `double_click`再生中に処理開始した場合は、`thinking`または`working`へ切り替える。
- `error`表示は通常のマウス反応より優先する。

最終的な優先順位はRuntime Event Dispatcherの定数または宣言的catalogで管理し、キャラクターパッケージ側から弱められないようにする。

## 13. Manifest example

```json
{
  "schemaVersion": 1,
  "id": "default_pet",
  "name": "WindowsPet",
  "version": "1.0.0",
  "author": "Lazy Life Dev.",
  "license": "Proprietary",
  "thumbnail": "thumbnail.png",
  "animations": {
    "idle": {
      "required": true,
      "playback": "loop",
      "frames": [
        {
          "id": "idle_a",
          "file": "animations/idle/a.png",
          "durationMs": 150
        },
        {
          "id": "idle_b",
          "file": "animations/idle/b.png",
          "durationMs": 150
        },
        {
          "id": "idle_c",
          "file": "animations/idle/c.png",
          "durationMs": 300
        },
        {
          "id": "idle_d",
          "file": "animations/idle/d.png",
          "durationMs": 150
        }
      ]
    },
    "double_click": {
      "required": false,
      "playback": "once",
      "frames": [
        {
          "id": "double_click_a",
          "file": "animations/double_click/a.png",
          "durationMs": 100
        },
        {
          "id": "double_click_b",
          "file": "animations/double_click/b.png",
          "durationMs": 500
        }
      ]
    }
  }
}
```

## 14. Validation and failure handling

### 14.1 Package validation

有効化前に次を検証する。

- `schemaVersion`対応
- package IDの形式と一意性
- 必須イベントの存在
- 各イベントの2〜10コマ
- 各フレームIDの一意性
- `durationMs`の範囲
- 相対パスのみ使用
- パッケージ外参照の拒否
- 画像デコード成功
- ファイル数、総容量、画像寸法の上限
- 禁止ファイルの有無

### 14.2 Failure behavior

- 編集中の一部エラーは該当行・フレームを強調し、保存を禁止する。
- 有効化時の重大エラーでは、以前の正常なキャラクター設定を維持する。
- 起動時に現在パッケージが壊れている場合は、同梱の既定キャラクターへフォールバックする。
- エラー内容に個人パスを不用意に表示・保存しない。
- 破損したパッケージを自動実行・自動修復しない。

## 15. Persistence

保存対象:

- 選択中キャラクターID
- キャラクターパッケージversion
- イベント定義
- フレーム配列順
- 各フレームの`durationMs`
- playback mode
- UIカテゴリの展開状態などの表示設定

保存しない:

- 元画像の外部絶対パス
- 一時ファイル
- ドラッグ中の状態
- プレビュー再生位置

保存は原子的に行う。新manifestを書き込んで検証した後に現在設定へ切り替え、失敗時は直前の正常状態へ戻せるようにする。

## 16. Security

- キャラクターは表示層であり、WindowsPet本体のTool権限を持たない。
- パッケージ内の実行可能コードを読み込まない。
- パス traversalを拒否する。
- シンボリックリンクやjunction経由でパッケージ外を参照しない。
- 画像デコーダへの過大入力を防ぐため、ファイルサイズ・寸法・総ピクセル数を制限する。
- 外部URLをmanifestの画像参照に使用しない。
- キャラクターイベントと、アプリ起動・ファイル変更などの機能割り当てを分離する。
- ダブルクリックなどに機能を割り当てる場合は、WindowsPet本体の設定として扱い、本体のPolicy／確認モデルを適用する。

## 17. Accessibility and usability

- ドラッグ＆ドロップだけに依存しない。
- キーボードまたはボタンによる「左へ」「右へ」移動を将来検討する。
- フレーム番号と表示時間をテキストでも表示する。
- ドロップ可能領域と禁止領域を視覚的に区別する。
- 必須イベント不足、コマ数不足、表示時間エラーを色だけで伝えない。
- 横スクロール可能であることを左右ボタン、スクロールバー、端部フェードなどで示す。
- 大量イベントでも設定画面全体が過度に縦長にならないようカテゴリ折りたたみを使用する。

## 18. Initial implementation phases

### Phase 1 — Data model and package loader

- manifest schema
- package path validation
- required event validation
- frame count and duration validation
- default character fallback
- current fixed animation loaderからpackage loaderへの移行

### Phase 2 — Basic editor

- イベント縦一覧
- フレーム横並び
- 「＋」から画像追加
- 個別`durationMs`編集
- 削除・差し替え
- プレビュー
- 保存・再読み込み

### Phase 3 — Drag and drop

- エクスプローラーからの画像追加
- 同一行内のフレーム並べ替え
- 挿入位置表示
- 横スクロール中のドラッグ
- 上限・不正形式・複数追加のエラー処理

### Phase 4 — Optional events

- `double_click`
- `hover_long`
- `single_click`
- `right_click`
- `drag_start`
- `drag_end`
- Runtime Event Dispatcher
- priority and interruption rules

### Phase 5 — Character selection and distribution

- キャラクター一覧
- thumbnail表示
- package import/export
- version表示
- license表示
- 安全な更新・差し替え

## 19. Acceptance criteria

- 既定キャラクター以外のキャラクターパッケージを読み込み、有効化できる。
- `idle`、`sleep`、`thinking`、`wave`がないパッケージを正常パッケージとして保存・有効化しない。
- 各イベントへ2〜10枚の画像を設定できる。
- 各画像に50〜5000msの表示時間を設定できる。
- イベントは縦に、フレームは各イベント行で横に表示される。
- 「＋」から画像を追加できる。
- エクスプローラーから対象イベント行へ画像をドロップして追加できる。
- フレームカードを同一行内で左右にドラッグして再生順を変更できる。
- 並べ替え後も画像と表示時間が一体で移動する。
- 10コマ時に追加を拒否し、理由を表示する。
- 2コマ時に1コマ未満になる削除を許可しない。
- 現在の順序と表示時間でプレビューできる。
- 未設定の任意イベントが発生してもエラーにならない。
- 破損パッケージ読み込み時にWindowsPetが起動不能にならず、既定キャラクターへ戻る。
- キャラクターパッケージから任意コードやPC操作を実行できない。

## 20. Decisions confirmed in this version

- キャラクターは最終的に差し替え可能にする。
- 現在設定済みのアニメーションイベントを必須とする。
- それ以外のイベントアニメーションは任意カスタマイズとする。
- ダブルクリックや長時間ホバーなどのイベントを追加可能にする。
- 1イベントは2〜10コマとする。
- 内部データは可変長配列とする。
- 各コマに画像とミリ秒単位の表示時間を設定する。
- イベント一覧は縦方向、各イベントの画像は横方向に配置する。
- フレームはドラッグ＆ドロップで左右に並べ替えられるようにする。
- 「＋」でファイル選択ダイアログを開いて画像を追加できるようにする。
- エクスプローラーから画像ファイルをドラッグ＆ドロップして追加できるようにする。

## 21. Open items

次は本仕様時点で未確定とする。

- PNG以外の正式対応画像形式
- キャラクター画像の推奨・最大寸法
- 1ファイルおよび1パッケージの容量上限
- 透過画像以外を許可するか
- `hover_long`の閾値とクールダウンを本体固定、ユーザー設定、パッケージ設定のどれにするか
- 初期版で正式提供する任意イベント一覧
- 複数画像選択・複数ドロップ時の確定順序
- 画像クリック時の既定操作を拡大表示または差し替えのどちらにするか
- キャラクターの名前、口調、音声など人格設定を同じパッケージへ含める時期
- package import/export形式

## 22. Document governance

Gitリポジトリ内のMarkdownを正本とする。

推奨配置:

```text
D:\work\WindowsPet\docs\WindowsPet_キャラクター・アニメーション仕様.md
```

ChatGPTプロジェクト共有ストレージには仕様書本体を複製せず、Git正本 `docs/WindowsPet_キャラクター・アニメーション仕様.md` を参照するよう案内するメモだけを置く。

運用規則:

1. Git側を先に更新する。
2. commit後の最新版を共有ストレージへ差し替える。
3. 共有ストレージ上で別編集しない。
4. file名、version、dateを一致させる。
5. 内容が競合した場合はGit側を正とする。
6. 本仕様の要点を`WindowsPet_設計仕様書`のRelated documentsまたはroadmapへ追加する。

## 23. Revision history

- **0.1.0 — 2026-08-07:** Initial baseline for replaceable character packages, required and optional animation events, 2–10 frame animations, per-frame millisecond duration, vertical event/horizontal frame editor, file picker addition, Explorer drag-and-drop addition, frame reordering, validation, runtime priority, and security separation.
- **0.1.1 — 2026-08-08:** Added researching/planning agent-state animation hooks so autonomous investigation can be represented visually without changing execution permissions.
- **0.1.2 — 2026-08-08:** Clarified unified WindowsPet intelligence identity across Local/Luna/Terra/Sol states and adopted stable Git-canonical documentation naming.

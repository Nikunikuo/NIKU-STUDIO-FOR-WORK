# NIKU STDUIO FOR WORK — Drama Studio

既存のNIKU H STUDIOレンダラーを変更せず、脚本、世界設定、参照素材とキャプション、H3 Prompt、生成ジョブ、動画Take、採用状態をProject単位で一元管理するローカル制作システムです。

人間はWeb UIで確認・修正し、Codexは同じProject ID / Revision / Digest / Focusへ束縛されたセッションから提案・保存・生成・再修正を行います。

## 起動

通常は次をダブルクリックします。

```text
C:\PROJECT_HUB\NIKU-STUDIO-FOR-WORK\drama-studio\Start-H3-Story-Studio.cmd
```

ブラウザを開かず、3サービスの起動・健康確認だけを行う場合:

```powershell
Set-Location -LiteralPath 'C:\PROJECT_HUB\NIKU-STUDIO-FOR-WORK\drama-studio'
.\Start-H3-Story-Studio.cmd -NoBrowser
```

起動スクリプトは健康な既存プロセスを再利用し、別アプリがポートを占有している場合はkillせず停止します。ログは `work\logs` に保存されます。

## ローカル構成

```text
Web UI                    http://localhost:3000
  └─ Story Sidecar        http://127.0.0.1:7864
      ├─ SQLite Project Core / revisions / sessions / handoffs
      ├─ registered assets / generated takes / media streaming
      └─ H3 REST adapter  http://127.0.0.1:7863
          └─ NIKU H STUDIO / ComfyUI / RTX 5090
```

Story Studioから既存H3へはSidecarだけが接続します。次のH3リポジトリは変更対象外です。

```text
C:\PROJECT_HUB\NIKU-STUDIO-FOR-WORK\h3_oss
```

## 制作ループ

1. Projectを選ぶ。
2. 概要・脚本・World Bible・素材キャプションを確認する。
3. 人間またはCodexがPromptや素材情報を修正する。
4. Project Coreが変更を新しいRevisionとして保存する。
5. H3へShot単位の生成ジョブを投入する。
6. 完了動画をProject配下へ保存し、SHA-256付きのAsset/Takeとして登録する。
7. Web UIで実動画を確認し、Takeを採用する。
8. 必要なら脚本・素材・Promptへ戻って同じループを繰り返す。

Web UIを再読み込みしても、Project CoreからPrompt、素材、生成Take、採用状態を復元します。Sidecarが使えない場合のUIデモTakeは、実H3生成物と明確に区別されます。

## Project Core

- SQLite WAL / foreign keys / atomic revision commits
- Project IDに束縛されたSession
- optimistic revision CASと409 conflict
- canonical state digest
- Entity: episode / scene / shot / asset / prompt / job / take
- approvals / focus / audit events
- immutable Handoff Capsule
- Project root外へのpath traversal・symlink breakout拒否

データベース、Project出力、Handoffはローカルにのみ保存されます。

## Codex CLI

```powershell
python scripts\storyctl.py health
python scripts\storyctl.py projects
python scripts\storyctl.py activate --project-id <PROJECT_ID> --agent-id codex-task
python scripts\storyctl.py context --project-id <PROJECT_ID> --session-id <SESSION_ID>
python scripts\storyctl.py handoff --project-id <PROJECT_ID> --session-id <SESSION_ID>
```

`patch`, `render`, `job`, `sync`, `h3-state` も利用できます。詳細は各コマンドの `--help` を参照してください。接続先はloopbackに固定され、全mutationには専用ヘッダーが付きます。

## Codex Skill

別タスクの新しいCodexがProjectを即座に復元できるSkillを次へインストールします。

```text
C:\Users\NIKU_\.codex\skills\operate-h3-story-studio\SKILL.md
```

Skillは、正確なProject activation、Revision/Digest確認、stale handoff拒否、提案後のatomic patch、H3生成・poll・sync、Take採用、次のHandoffまでを定義します。

## 開発とテスト

Node.js 22.13以上とPython 3を使用します。

```powershell
npm install
npm run dev
npm run typecheck
npm run lint
npm test
```

`npm test` はproduction build、SSR/UI契約、起動スクリプト、TypeScript契約、Sidecar client、Project Core、HTTP API、H3 adapter、render service、CLIをまとめて検証します。

# NIKU STUDIO FOR WORK

ローカルH3動画生成と、Project CoreベースのDrama Studioを一つのリポジトリにまとめたWindows向け統合版です。

```text
NIKU-STUDIO-FOR-WORK/
├─ h3_oss/                         NIKU H STUDIO（H3レンダラー）
├─ drama-studio/                   NIKU STDUIO FOR WORK（物語・素材・脚本・Take管理）
└─ skills/operate-h3-story-studio  CodexのProject操作・生成・QA Skill
```

## 起動

初回だけH3側の固定モデルとComfyUI runtimeをセットアップします。モデル重みはGitには含めません。

```powershell
Set-Location -LiteralPath 'C:\PROJECT_HUB\NIKU-STUDIO-FOR-WORK\h3_oss'
.\Setup-H3-Studio.cmd
```

H3単体を起動する場合は、次を実行します。

```powershell
.\Start-H3-WebUI.cmd
```

Drama StudioをH3とSidecarごと起動する場合は、リポジトリ直下から次を実行します。

```powershell
Set-Location -LiteralPath 'C:\PROJECT_HUB\NIKU-STUDIO-FOR-WORK'
.\Start-NIKU-STUDIO.cmd
```

`drama-studio\scripts\start-story-studio.ps1` は、同じリポジトリ内の`h3_oss`を見つけて、H3（7863）、Story Sidecar（7864）、Drama Studio Web UI（3000）を順番に確認・起動します。`-NoBrowser`を渡すとブラウザを開きません。

## 役割分担

- **NIKU H STUDIO**: H3の日本語/community prompt flow、参照素材、EasyCache、SageAttention、ComfyUI native workflow、動画・同期音声生成。
- **NIKU STDUIO FOR WORK**: Project管理、World Bible、人物・舞台・小物素材、キャプション、脚本、プロンプト、生成候補Take、採用Take、修正ループ、Codex handoff。
- **Sidecar**: Project Coreのrevision/CAS、asset登録、H3 jobの仲介、動画アーティファクトとend-frameの登録。

H3とDrama Studioの間は、既存の`http://127.0.0.1:7863` REST境界を使用します。H3の内部ヘッダー名・job識別子・referenceタグ契約は互換性のため維持し、製品表示だけをNIKU H STUDIOへ変更しています。

## テスト

H3側:

```powershell
Set-Location -LiteralPath 'h3_oss'
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

Drama Studio側:

```powershell
Set-Location -LiteralPath 'drama-studio'
npm install
npm test
```

## Codex Skill

統合版に同梱したSkillは、Project ID / Revision / Digest / Focusへの固定、`@素材名`解決、H3へのcommunityフロー、i2v/Omni選択、失敗コード別リトライ、候補QA、採用タイムライン、最終結合監査を扱います。Codexへインストール済みのSkillを優先し、同梱版はリポジトリと一緒にレビュー・更新するための正本コピーとして利用できます。

## ライセンスとモデル

H3本体のMITライセンス、MiniMax H3/Qwen/ComfyUI/SageAttentionなど各依存物のライセンス・モデル利用条件は`h3_oss`内の`LICENSE`、`MODEL_TERMS.md`、`THIRD_PARTY_NOTICES.md`を確認してください。巨大なモデル重み、仮想環境、生成物、ローカルProjectデータはGitへ追加しません。

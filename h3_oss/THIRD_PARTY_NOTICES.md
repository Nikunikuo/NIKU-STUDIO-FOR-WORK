# Third-Party Notices / サードパーティ通知

最終確認: 2026-08-05

H3-STUDIO本体のMIT Licenseは、セットアップ時に取得される上流ソフトウェア、Python package、モデル、重みを再ライセンスするものではありません。それぞれの著作権表示とライセンスが維持されます。モデル固有の条件は [`MODEL_TERMS.md`](./MODEL_TERMS.md) を参照してください。

This project's MIT License does not replace the licenses of downloaded third-party components.

## 主なコンポーネント / Principal components

- **ComfyUI** — 固定commit `14b05228cef127ce529bc0c08660770d4af3e9a8`をセットアップ時に取得し、別processとして実行します。License: GNU General Public License v3.0. [Pinned source and license](https://github.com/Comfy-Org/ComfyUI/blob/14b05228cef127ce529bc0c08660770d4af3e9a8/LICENSE)
- **Comfy-Org workflow_templates** — 固定commit `7653f1cdef1d92394b6ef9946018c0a8aa4136b8`をワークフロー設計の参照元としています。H3-STUDIOへvendorせず、セットアップもこのGitリポジトリの独立checkoutを作成しません。License: MIT. [Pinned source and license](https://github.com/Comfy-Org/workflow_templates/blob/7653f1cdef1d92394b6ef9946018c0a8aa4136b8/LICENSE)
- **SageAttention** — upstream License: Apache License 2.0. [Official source and license](https://github.com/thu-ml/SageAttention/blob/main/LICENSE) Windowsではupstream公式binaryではなく、第三者`woct0rdho`による固定wheel `2.2.0+cu130torch2.10.0andhigher.post6`を取得します。[Third-party Windows wheel release](https://github.com/woct0rdho/SageAttention/releases/tag/v2.2.0-windows.post6) セットアップは16,656,067 bytesおよびSHA-256 `1635283f5c01ec3cda58a784d0d7eabbcaffaf9511d1b263db4750e1ed7958bb`を検証します。
- **triton-windows** — version `3.7.1.post27`。License: MIT. [Official project and license](https://github.com/woct0rdho/triton-windows/blob/main/LICENSE)
- **Qwen3-4B-Instruct-2507** — 既定のcommunity prompt plannerです。日本語自然文を公開ローカル成功例型の英語制御ブロックへ意味展開する別processで使用し、公式Full-Reference Rewrite Guideは意味・参照・時系列のguardrailとして参照します。公式6-section rewrite形式と`<d>`タグ自体は出力せず、完了後はH3／ComfyUI起動前にprocessを終了します。セットアップは固定revision `cdbee75f17c01a7cc42f958dc650907174af0554`から許可リスト9ファイル（8,056,459,158 bytes）だけを取得し、サイズとSHA-256を検証します。重みはGitへ含めません。License: Apache License 2.0。[Pinned model card](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/blob/cdbee75f17c01a7cc42f958dc650907174af0554/README.md) / [Pinned license](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/blob/cdbee75f17c01a7cc42f958dc650907174af0554/LICENSE)
- **Qwen3-VL** — MiniMax H3のtext encoderはQwen3-VL-32B系です。MiniMax公式ライセンスはこのencoderをApache License 2.0として明記しています。Qwen側のライセンスとモデルカードも確認してください。[Qwen3-VL license](https://github.com/QwenLM/Qwen3-VL/blob/main/LICENSE) / [Qwen3-VL-32B-Instruct model card](https://huggingface.co/Qwen/Qwen3-VL-32B-Instruct)
- **PyTorch / torchvision / torchaudio** — セットアップ時に公式wheelを取得します。PyTorch本体はBSD-style licenseで、各projectの同梱noticeも適用されます。[PyTorch license](https://github.com/pytorch/pytorch/blob/main/LICENSE)

## その他の依存関係 / Other dependencies

[`requirements.webui.txt`](./requirements.webui.txt)、[`requirements.comfy.txt`](./requirements.comfy.txt) に記載された各Python packageは、それぞれ独自のライセンスで配布されています。H3-STUDIOはそれらをGitへvendorせず、隔離virtual environmentへpackage indexからインストールします。再配布や製品組み込みを行う場合は、実際にインストールされたversionのmetadata、license、NOTICEを確認してください。

README／`SETUP_STATUS.md`の履歴資料には、原因調査時に使用したHugging Face Diffusersの固定SHAやLFM翻訳実験が記録されています。これらは現行HEADの配布物、セットアップ対象、実行時依存ではありません。リンクと測定値は過去の検証条件を解釈するための来歴としてのみ残しています。

本書は依存関係の把握を助けるための非網羅的な一覧であり、法的助言ではありません。

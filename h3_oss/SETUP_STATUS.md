# SETUP STATUS

> [!IMPORTANT]
> 現行HEADの実行経路は、`community`／`raw_en`から`native_clean`のComfyUI native H3 nodesへ渡す構成だけです。本書は再現性のため過去の測定結果も削らず保持しています。「履歴」「Legacy」「旧」と明記したDiffusers、Context-IR、`direct`／`official_en`、LFM、tokenizer互換のコード・モデル・セットアップ導線は現行HEADから削除済みで、このcheckoutから再実行できません。

## Current ComfyUI route

- 状態: ComfyUI native backendへの移行完了。browser UI→private pinned ComfyUI child→H.264/AAC出力・全decodeまで実E2E成功。旧Diffusers結果は比較記録として保持
- 公開確認: 2026-08-03 11:35 JST、公式Hugging Faceで匿名取得可能
- ModelScope公開確認: revision `29139ad62f28479297e305d690ee1521042133d4`、選択60パス存在、READMEを除く59ファイルのサイズ・SHA-256がHugging Face固定revisionと一致
- 固定取得revision: `af0fe5abe6fd50d632b65a82fef321c4c5c1f249`（初回公開commit）
- 2026-08-04再確認時の公式head: `5d9b308a59ab12e67147f191e184baf704185bd1`。モデル取得は初回公開revisionへ固定し、更新されたPrompting Guidanceはraw prompt作成の参照に使用
- ライセンス: MiniMax H3 Community License Agreement（日本は適用地域、EU／英国／韓国／米国は除外）
- 対象variant: FL2VA＋Ref2VA（Omni）
- 通常生成方式: NIKU H STUDIO custom UI＋private ComfyUI native H3 backend。各requestでJobManagerが動的loopback portへfresh childを起動し、`--cache-none`で前jobのmodel stateを持ち越さない。Web UI→worker／worker→ComfyUIの2段をWindows Job Objectへ所属させ、親が先にexitしても`KILL_ON_JOB_CLOSE`で子孫をOS回収。固定8188番では常駐させない
- 通常プロンプト方式: 公開ローカル成功例を実行形の基準、公式Base／Full-Reference guideを意味・参照・時系列制約の基準にするcommunity plannerを既定化。日本語自然文を別processの`Qwen3-4B-Instruct-2507`でstrict JSON planへ意味展開し、コード側が公開例型の英語`Style / Reference material / Scene / Shot / Audio`ブロックへ決定論的にrenderする。参照タグ、時刻、数値、Seed、解像度、audio policy、台詞原文をコードで保持・検証し、実際の日本語台詞だけを普通の二重引用符内へ1回戻す。検証済みpromptはcustom tokenizer shimなしの`native_clean` ComfyUI workflowへそのまま渡し、planner processはH3起動前に終了。元入力と実効英語promptを`request.json`／`execution_request.json`／`prompt_processing/`へ監査保存
- ComfyUI SHA: `14b05228cef127ce529bc0c08660770d4af3e9a8`
- workflow templates参照元SHA: `7653f1cdef1d92394b6ef9946018c0a8aa4136b8`（設計の出典。通常setupでは未使用のcheckoutを作らない）
- Comfy向けモデル: `Comfy-Org/MiniMax-H3` revision `0543966fbdce5ba05709a8f2031c94bdba629b4a`
- 履歴資料のLegacy Diffusers SHA: `abc5e9bf71fd38f53cd471bc3acaa84bc5ecbfdc`（現行HEADの依存ではない）
- GPU: NVIDIA GeForce RTX 5090 32GB
- RAM: 約253GiB
- Cドライブ空き: 約3.48TB（構築開始時）
- Python環境: Web UI `.venv`とComfyUI `.comfy-venv`を分離。Comfy側はPython 3.12.13、torch 2.13.0+cu130／torchvision 0.28.0+cu130／torchaudio 2.11.0+cu130をCUDA 13.0 indexから`--no-deps`で固定し、torchao 0.17.0を使用。CUDAと32kHz audio resampleを確認済み
- ComfyUI上流実装: `14b05228cef127ce529bc0c08660770d4af3e9a8`をdetached checkoutで固定済み
- ComfyUIモデル取得: 5/5、63,440,965,087 bytes（約59.08GiB）
- ComfyUIモデル検証: ローカルSHA-256／byte数が固定HF revisionのLFS SHA-256／sizeと全件一致
- ComfyUI model lock: `comfy_models.lock.json`
- 必須prompt planner: `Qwen/Qwen3-4B-Instruct-2507` revision `cdbee75f17c01a7cc42f958dc650907174af0554`。実行最小9ファイル8,056,459,158 bytesを`models/prompt_planner/Qwen3-4B-Instruct-2507`へ配置し、`prompt_planner.lock.json`のサイズ／SHA-256へ全件一致。Apache-2.0、追加クリック同意なし。offline AutoConfig／fast Tokenizer／chat templateとCPU BF16全重みロードを確認し、4,022,468,096 parametersを検証
- ComfyUI起動前検査: `setup_comfy.ps1 -VerifyOnly -SkipModelHash`成功（固定ComfyUI checkout、CUDA、torchao、32kHz audio resample、SageAttention kernel、H3 5ファイル／63,440,965,087 bytes、Qwen planner 9ファイル／8,056,459,158 bytes、offline config／tokenizer、NIKU H STUDIO管理のplanner来歴marker）。通常起動は約17.65秒のfast probeを実行し、Qwen全重みCPU loadと完全model SHA再検査は初回setupまたは明示検証時だけ
- ComfyUI FL2VA: `minimax_h3_fl2va_pruned_int8_convrot.safetensors`、20,970,379,616 bytes、SHA-256 `e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a`
- ComfyUI Ref2VA: `minimax_h3_ref2va_pruned_int8_convrot.safetensors`、20,970,379,616 bytes、SHA-256 `9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779`
- ComfyUI Qwen: `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`、15,687,142,551 bytes、SHA-256 `35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6`
- ComfyUI audio VAE: `minimax_h3_audio_vae_fp32.safetensors`、605,254,808 bytes、SHA-256 `8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48`
- ComfyUI video VAE: `minimax_h3_video_vae_fp16.safetensors`、5,207,808,496 bytes、SHA-256 `7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522`
- EasyCache: native node。通常生成の初期値は`community`（おすすめ：控えめ）。公開例presetはreuse threshold 0.20／sampling 15%～95%、`off`は品質比較用、`conservative`（より慎重）と`balanced`（速度優先）は任意選択、12 steps未満は自動OFF。Sage定常OFF 39.01秒に対し別条件の0.20併用39.67秒で、8/20 skip・表示1.67×でもPrompt総時間は同等。近似による映像・音声差もあり、通常はcommunityを使いつつ、品質確認時はoffと比較する
- SageAttention: `2.2.0+cu130torch2.10.0andhigher.post6`＋`triton-windows 3.7.1.post27`を標準ON。固定Windows wheel 16,656,067 bytes／SHA-256 `1635283f5c01ec3cda58a784d0d7eabbcaffaf9511d1b263db4750e1ed7958bb`をsetupが取得・検証。小型kernel smokeはfinite／repeat equal、SDPA比cosine 0.999344。fallbackは起動前に`$env:H3_ATTENTION_BACKEND='pytorch'`。fallback時はSage import／version／kernel検査を意図的にskipし、PyTorch経路の起動前検査成功を実機確認済み
- Browser E2E: `320×192`／124 frames／Draft 8／EasyCache OFF、初回PyTorchを含むtotal 86.031秒。5.167秒、H.264 124 frames＋AAC 331,776 total samples（165,888／channel）／32kHz stereoを全decode
- Sage標準browser E2E: fresh private childでtotal 44.422秒（Comfy prompt 36.51秒、起動6.813秒、denoise 19秒、Video VAE 6.10秒、全decode検証0.110秒）。`backend=comfy`／`attention_backend=sage`をjobへ保存し、UIにも表示
- 実キャラクター2画像Omni E2E: 各1448×1086の設定画を`<Picture 1>`／`<Picture 2>`、高精度`max`で入力。Job Object／listener ownership安定化後の最終試験は`320×192`／124 frames／Draft 8／EasyCache OFF、total 34.375秒（Comfy prompt 28.047秒、起動6.079秒、検証0.125秒）。5.167秒、H.264 124 frames＋AAC 331,776 total samples（165,888／channel）／32kHz stereoを全decode。2人の主要な外見上の差を保った動きを目視確認。低解像度Draftなので細部同一性の品質保証には使用しない
- 履歴・削除済みraw formatter dialogue baseline Omni E2E: 指定されたミルちゃん／ハミちゃん設定画2枚を`match`参照し、自然な日本語Cut本文`<Picture 1>の女性は低く落ち着いた女性の声で「こんにちは。」と一度だけ言う。`を当時の決定論的formatterへ入力。job `6c89d54523eb`、`320×192`／124 frames／20 steps／EasyCache OFF／`auto→simple`、total 58.282秒（Comfy prompt 50.15秒、denoise約34秒、起動6.844秒、検証0.078秒）。329,657 bytes／5.167秒、H.264 124 frames＋AAC 32kHz stereo 331,776 total samplesを全decode。実音声のactive segmentは3.34～4.20秒の1区間だけで、Whisper tiny日本語ASRは正確に`こんにちは`、追加制御文・連続ナレーション・謎言語なし。測定値は保持するが、このformatterは現行HEADから削除済み
- 履歴・旧`direct`日本語＋standalone Audio原因A/B: 同じミルちゃん／ハミちゃん2画像、同じ日本語prompt、`672×384`／124 frames／20 steps／seed `1650701047`で比較。Whisper tiny日本語ASRは、参照元`12.mp3`が`はいつなまよ、マヨネージです`、AudioをRef2VAへ渡したjob `a3a00555aba7`が`はいつなまよ、マヨネージです!`、Audioだけ外したjob `bc799270a65e`が`こんにちは`だった。Audioありはtotal 228.641秒で屋内背景・長い口動作、Audioなしはtotal 162.641秒で屋外ベンチ・固定正面・手振りへ改善。両方とも5.167秒、H.264 672×384 124 frames＋AAC 32kHz stereoを全decode。この同seed A/Bにより、日本語直渡しではなく全波形Audio条件が主因と判定。旧`direct`入力モード自体は現行HEADから削除済み
- 履歴・旧formatter上の`dialogue_priority`実ルートE2E: 同じ2画像、同じ`12.mp3`添付、同じ日本語prompt／`672×384`／124 frames／20 steps／seed `1650701047`でjob `64a716138f74`を生成。元添付は`image,image,audio`で監査保存し、実行用`references`は`image,image`、`standalone_audio_conditioning=false`、実効文のAudio tag 0件、native台詞1件を確認。total 110.938秒（generation 99.078秒、startup 11.375秒）、306,246 bytes／5.167秒、H.264 672×384 124 frames＋AAC 32kHz stereoを全decode。Whisper tinyは日本語固定／自動判定とも`こんにちは`。目視では2人の人物差・夏服、固定正面、左人物の手振りを維持。海そのものは明瞭でなく公園のベンチ背景になったため、音声修正の成功と構図追従の改善は確認するが、背景完全追従とは扱わない。音声条件を除外した比較結果は現行ポリシーの根拠として保持するが、当時のformatter自体は現行HEADに含まれない
- 公開日本語成功例native baseline: note記事で配布された英語promptをbyte単位で`native_clean`へ渡し、`864×480`／124 frames／20 steps／`simple`／EasyCache OFFで`community-baseline-mayu.mp4`を生成。total 81.25秒（generation 74.11秒）、1,002,960 bytes／5.167秒、H.264 124 frames＋AAC 32kHz stereoを全decode。Whisper tinyは引用された日本語台詞だけを検出し、Storyboard制御文の読み上げなし
- community planner実データ: 問題が再現した日本語5-Cut promptを固定Qwen3-4Bで変換。初回実測42.656秒（load 9.173秒、generation 33.456秒、1,088 input tokens、724 content tokens、EOS到達）。参照画像はidentity／face／body／hairだけ、指定したtank top＋shortsで参照衣装を上書きし、Shot順`1,2,4,5,6`、120kg／200kg、声質、普通の引用符内の日本語12文字を完全保持。日本語制御文、`<d>`、追加発話なし。同一入力はSeed／EasyCacheをcache keyから除外して再利用
- community＋native_clean長尺Omni E2E: job `d5d77b8e52a8`、ハミちゃん設定画1枚、`864×480`／345 frames（14.375秒）／20 steps／seed `1720212229`／EasyCache OFF。作成から完了599.074秒、planner 42.484秒、engine total 556.453秒（Comfy prompt 547.98秒）。1,625,248 bytes、H.264 345 frames＋AAC 32kHz stereo 921,600 total samplesを全decode。Whisper tinyの全区間ASRは短い`あ！`、9～12秒の高RMS区間は長い`うー`系の力み声だけで、日本語制御文・Cut説明・連続ナレーションなし。12秒以降の低RMS区間で出た定型句は発話区間の約1/13の音量で、無音寄り区間のASR hallucinationとして扱う。目視ではgym、tank top＋shorts、treadmill、weights、暗転後protein drinkへ大枠追従した一方、dumbbell squat／deadlift／barbell liftの種目境界がbarbell squat系へ混ざり、参照眼鏡も消失したため、細かな動作・小物の完全追従成功とは扱わない
- camera geometry release fix: 上記E2Eの実効promptに残った`low-angle upward`＋`positioned above`と`low-angle from above`の自己矛盾を、各Shotのframing＋camera単位で`CAMERA_DIRECTION_CONFLICT`として拒否。source `Cut N`の仰角／煽り／俯瞰は同番号`Shot N`で低位置＋上向き／高位置＋下向きの両条件を要求する。修正後の同一実データは再試行なし37.359秒でcompileし、Shot 1を低位置から上向きへ統一、台詞12文字・参照・衣装・数値を保持。cache compiler revisionを`2026-08-05-native-clean-v3-camera-geometry`へ更新し、旧cacheを再利用しない。Web UI serverもこのrevisionで再起動済み
- 履歴・削除済みContext-IR比較GPU smoke: 同じ2画像、ローカルcompiler 16ms、Omni Draft 8を`auto→simple`へ解決し、total 49.234秒（generation 42.203秒、起動6.828秒、検証0.109秒）。出力`eba23d5d126e.mp4`は327,834 bytes／5.167秒／320×192、H.264 124 frames＋AAC 32kHz stereo 331,776 total samplesを全decode。指定した日本語台詞は`<d>[Japanese] こんにちは。</d>`として1回だけ実効IRへ含まれ、追加ナレーション禁止を併記。このcompiler、`<d>` wrapper、tokenizer互換経路は現行HEADから削除済み
- PyTorch比較: `640×384`／124 frames／20 stepsの初回Prompt 231.31秒（denoise 36.6秒、Video VAE 180.66秒）。別runはPrompt 117.88秒（EasyCache 7/20 skip・表示1.54×、Video VAE 63.34秒）
- Sage固定server A/B: 初回compile込みPrompt 54.36秒、定常run 39.01秒。Sage＋EasyCache 0.20は39.67秒（denoise 14秒、8/20 skip・表示1.67×）でOFFと総時間同等
- Sage出力検証: 全出力H.264 124 frames＋AACを全decode、黒画面／ノイズなし。同seed通常版と目視ほぼ同等だが、数値的・byte単位の完全一致とは扱わない

## Historical: removed Legacy Diffusers assets and smoke tests

以下は削除済み旧Diffusers経路の資産検証・スモーク結果です。当時の固定SHA、byte数、hash一致、失敗と修正の経緯を保持するための履歴であり、記載されたworker、download／conversion script、manifest pathは現行HEADに存在せず、現在のセットアップ対象でもありません。

- Legacy上流実装: Diffusers `abc5e9bf71fd38f53cd471bc3acaa84bc5ecbfdc` をdetached checkoutで固定済み
- 公式重みダウンロード: 完了（60/60、144,051,067,662 bytes）
- 公式ファイル検証: サイズ・LFS SHA-256全件一致、エラー0
- Diffusers変換: 完了（Transformer 61.73GiB、video VAE 9.70GiB、audio VAE 0.56GiB）
- 変換manifest: 58ファイル、144,051,142,551 logical bytes、SHA-256記録済み
- 共有Qwen／processor／tokenizer: 公式ファイルへのNTFSハードリンクで重複保存を回避
- 公式Ref2VA追加取得: 16ファイル、66,280,525,570 bytes、サイズ・LFS SHA-256全件一致、エラー0
- Ref2VA変換: `transformer_ref` 15ファイル、66,280,569,783 bytes、SHA-256記録済み
- Ref2VA manifest: `artifacts/official_ref2va_manifest.json`／`artifacts/converted_ref2va_manifest.json`
- Ref2VA追加ストレージ: 公式＋変換済みで約123.46GiB
- スモークテスト: 成功（320x192、124 frames、2 sigma points、seed 42）
- スモーク出力: H.264 video 124 frames＋AAC audio 331,776 samples、5.175秒、両ストリーム全decode成功
- スモーク所要時間: 221.62秒（denoise 1 forwardは5.14秒）
- スモーク初回試行: 重みロード前に停止。PR文書の`low_cpu_mem_usage=False`を同SHAの量子化ローダーが拒否する不整合を確認。サポートされる`True`へ修正し、再試行は成功

## NIKU H STUDIO

- NIKU H STUDIO Web UI: `http://127.0.0.1:7863` localhost限定、Text／Image／Frames／Omni、順番付き参照、進捗リング、キャンセル、履歴、再生、ダウンロード、出力フォルダ表示
- NIKU H STUDIO Web境界: numeric loopback以外のHost、cross-site `Sec-Fetch-Site`、専用`X-H3-Studio-Request`ヘッダーのない更新、Hostと一致しないOriginを拒否。CSP／no-store／frame拒否ヘッダーも付与し、DNS rebinding・cross-site GPU job投入を防止
- NIKU H STUDIO upload境界: `/api/jobs`の宣言済みaggregate bodyは8GiB、個別素材は2GiB上限。Omniの12件／画像9／動画3／音声3制限と非Omni参照拒否をジョブディレクトリ作成前に検査
- NIKU H STUDIO process境界: Windows Job Object `KILL_ON_JOB_CLOSE`を2段で使用。親先行exit後の孤児回収、終了冪等性、ProcessJob作成失敗時のspawn禁止、cancel／runner競合で次engineを誤停止しないことを実機・unit testで確認。private Comfy health後はlistener PIDがspawn tree内であることも検査
- NIKU H STUDIO参照UI: 素材タイプ別の公式Picture／Video／Audioタグ自動採番、並べ替え再採番、クリック挿入、参照動画の埋め込み音声は通常経路では使用しない旨、およびstandalone Audioポリシーを表示
- NIKU H STUDIO参照精度UI: Omni専用に`高速（match）`／`高精度（max）`を選択。matchは生成canvas相当の総画素へdownscale-only、maxはupscaleせず短辺2048上限。選択値をrequest／jobへ保存し、再利用時に復元
- NIKU H STUDIO prompt processing: 画面からの新規jobは既定の`community` plannerで、日本語自然文を固定Qwen3-4B text-only workerへ渡し、strict schemaを経て公開例型の英語`Style / Reference material / Scene / Shot / Audio`ブロックへrenderする。台詞は事前退避し、翻訳せず普通の二重引用符へ1回だけ復元。参照集合／数値／時刻／audio policy／台詞完全一致／日本語残留範囲をコードで検証し、失敗時は生成前に停止する。H3には`native_clean` profileで実効promptをbyte単位のまま渡し、custom tokenizer shimを使わない。既に整えた英語を無変更で渡す公開`raw_en`と合わせ、入力方式はこの2つだけ
- NIKU H STUDIO prompt監査: 元`request.json`はbyte単位で不変、派生`execution_request.json`、`prompt_processing/final_prompt.txt`、`report.json`をatomic保存。入力／出力SHA-256、planner revision、cache、検証診断、自動調整を記録し、削除済みcompiler用artifactは新規jobへ作らない
- NIKU H STUDIO詳細UI: compiler／入力ガード情報は通常入力画面へ出さず、完了／失敗／cancel後の「生成の詳細を見る」に実効プロンプト、自動調整、参考情報、技術情報を欠損安全に表示し、内容は`textContent`だけで描画
- NIKU H STUDIO解像度UI: 固定pixel一覧を縦横比（16:9／9:16／1:1／4:3／3:4）×解像度段階（Preview／SD 480p相当／HD 720p相当／Native 768p）の2軸へ変更。実width／heightはserver共通catalogから取得し、H3の32px alignmentを維持。9:16×HDの送信値`736×1312`、軽量Previewは縦横比を維持して9:16なら`384×672`、旧`640×384` jobは16:9×Preview`672×384`への最寄り復元を実ブラウザ／DOM／FormDataで確認
- NIKU H STUDIO進捗UI: Qwen解析／参照VAE／レイアウト／denoise／映像復元／音声復元／MP4化を分離。実イベント間だけ次段階未満の上限付き推定を表示
- NIKU H STUDIOプロンプト再利用: 成功・失敗・キャンセルを含む永続ジョブ履歴から最大20件の重複なしプロンプトと音響指示を復元
- NIKU H STUDIO音響UI: 通常はメインプロンプトの各Cutへ台詞・声質・環境音・効果音を直接記述。音声設定は折り畳み式の任意詳細へ下げ、音の主役、BGM、台詞上書き、全体音響補足、出力音量だけを保持。実台詞だけを原文の普通の二重引用符へ局所復元し、声質・話者・停止は引用符外の英語、環境音・効果音はpositiveな具体音としてrender。台詞なし／指定台詞だけ／自動のaudio policyを明示し、曖昧な発話cueや繰り返し禁止文を生成promptへ入れない。最終出力音量はComfyUI core `AudioAdjustVolume`のraw dBゲインとして適用し、normalization／clipping preventionは行わない。+dBは元peak次第でclipし得る。各設定を永続ジョブ履歴へ保存・復元
- NIKU H STUDIO負荷UI: `960×544`・約5秒・Draftを基準に出力側の相対負荷を表示。Omni参照は倍率外の追加負荷として明記し、軽量プレビュー設定ボタンを提供
- NIKU H STUDIO workflow profile: `native_clean`のみ。ComfyUI native H3 nodesだけを許可し、custom node、`<d>` marker、tokenizer改変を読み込まない
- NIKU H STUDIO scheduler: 非表示`auto` policyでFL2VA（Text／Image／Frames）とRef2VA（Omni）の両方を、公開ComfyUI workflowの実設定と同じ`simple`へ解決。事前解決値とworkflow metadataの一致をworkerで再検証し、requested／effective値をjob詳細へ保存。同じ2画像・同じseed・同じ実効promptの320×192／8 steps実動画A/Bで、`normal`は明瞭な未denoise色ノイズが残り、`simple`は人物・衣装・背景を正常復元。20 steps音声A/BでもRef2VA `normal`が指定台詞から逸脱したため、`normal`の自動選択を廃止
- NIKU H STUDIO参照動画音声: raw経路では埋め込み音声を偶発的にコピーしないよう`ignore`固定。公式Ref prompt guideは、声色・リズム・感情・話し方だけを参照する場合、元音声の台詞を出力へ持ち込まないよう明示している。一方、公開ComfyUI nodeはspeaker／timbre embeddingを抽出せず、入力波形全体をAudio VAE latentとして渡す。この公開経路ではvoice-onlyを物理的に強制できないため、NIKU H STUDIOは確率的なprompt誘導として扱う。明示台詞と併用する既定`dialogue_priority`ではAudioを実行用`references`と実効promptから外し、元添付と除外理由は監査保存。`full_content`を明示選択した場合だけ全波形を渡し、元発話・間・場面が指定を上書きし得る診断を保存。動画音声を個別にspeaker／timbreだけへ分離する機能はない
- 履歴テスト基準: 旧270件中269件成功（Windows directory symlink向け1 skip）は、削除済み`direct`／`official_en`／Context-IR経路の移行前基準。community planner／`native_clean`移行後、cleanup前の最終件数はRepository節へ記録
- 音声仕様確認: 公開H3入力／ComfyUI H3 nodeの生成条件には、独立したvoice strength、audio guidance、生成音量パラメータはない。映像と32kHzステレオ音声を共有Transformerで共同生成し、NIKU H STUDIOの最終出力音量は共同生成後の波形へ適用するpost-processのraw dBゲイン
- 速度差確認: Diffusers文書は960×544を1344×768より約2.3倍／step高速と記載。公式初回OSSはfull attentionのみ、sparse attentionは今後公開予定。公式SGLangのconsumer最速検証は2×RTX 5090であり、単一5090 CPU offloadとは非同条件

## Historical: removed Legacy Diffusers measurements

以下も削除済み旧Diffusers実装の履歴です。現行のpruned int8／NVFP4-AWQ ComfyUI経路の性能値ではなく、同じcheckoutから再実行できるbenchmarkでもありません。

- Web UI i2v実生成: 成功、215.86秒、5.175秒H.264＋AAC、124 video frames／331,776 audio samplesを全decode
- Web UI Omni実生成: 成功、234.42秒、5.175秒H.264＋AAC、124 video frames、画素標準偏差57.79、331,776 audio samples、audio RMS 0.00517
- Qwen障害原因: 固定Diffusers SHAがH3に必要な50層目中間出力のために64層すべてを実行し、65 hidden statesを保持。1448×1086画像2枚を短辺2048へupscaleした旧経路は10,880 vision tokens／sequence 11,206で、hidden保持だけでBF16約6.95GiB
- Qwen安定化: 正式マージ済みSGLang／ComfyUI実装に合わせ、checkpointの64層を構築してから削らず最初から50層だけをロード。final norm／LM headなし、`output_hidden_states=False`、SDPA、同期block offloadへ変更
- Ref2VA画像安定化: マージ済みComfyUIの品質優先`max`方式に合わせ、upscaleせず短辺2048を上限に32px整列。実画像2枚は各1448×1086→1440×1088、合計3,060 vision tokens／sequence 3,386
- Qwen構築ベンチ: checkpointの1,058 weight群中904群をロード、25.143秒（旧64層構築33.251秒）。後半14層の`UNEXPECTED`表示は意図した未使用weightの読み飛ばし
- Qwen実画像ベンチ: 同一workerで25.776秒／15.509秒、CUDA peak allocated 7.524GiB／7.522GiB、NaN／Infなし、連続runのembedding／token tagはbyte単位で完全一致
- Ref2VA安定化E2E: 同じ2画像・同じprompt、320×192、124 frames、2 grid pointsを同じpipelineで2回連続成功。各5.175秒H.264 124 frames＋AAC 331,776 samplesを全decode、映像標準偏差62.6301、audio RMS 0.0218702／peak 0.1550
- Ref2VA再現性: 2本とも208,812 bytesで、MP4／previewともbyte単位で完全一致
- 冷間ロード実測: process peak RSS 217.28GiB／peak paged memory 239.69GiB（旧64層構築peak RSS 226.13GiB）。安定化ゲートは空き物理RAM 225GiB、commit余力300GiB、通常空きVRAM 24GiB
- 高負荷ゲート: 画素数×framesが2.5億以上では空きVRAM 29GiBを要求し、pipeline再利用時もdevice全体の空きを再確認
- 最大設定実測: 1344×768・345 frames・2 grid pointsはQwen修正後もTransformer denoise 1 forwardが12分48秒超、VRAM最大約31.7GiBで手動停止。full attention本体の負荷であり、20 grid pointsを単一5090の20分経路とは扱わない
- VAE配置: video VAEはleaf-level CPU offload、audio VAEはleaf offloadでdecoder device mismatchを起こすため検証済みのGPU常駐（約0.56GiB）
- モデル切替: FL2VA↔Ref2VA時に生成ワーカーPIDが変わることを検証済み。同variant連続生成はロード済みモデルを再利用
- 実測メモリ: 不完全な同一プロセス内切替ではページファイル約246GiBまで増加したため不採用。ワーカー完全再起動後は空きRAM約230GiBまで回復

## Repository

- 公開対象: 独自コード、設定、lock、ドキュメントのみ。モデル重み、参照素材、生成物、仮想環境、上流checkout、cacheはGit対象外
- cleanup前公開監査（履歴）: tracked＋非ignore untrackedの配布候補82ファイル、約1.29MiB、10MiB超ファイルおよびモデル／動画／画像／音声binaryなし。当時の候補には、後に削除した旧Diffusers／Context-IR／LFM runtime、`prompt_translator.lock.json`、対応テストも含まれていた
- cleanup前unit tests（履歴）: 339件中338件成功、Windowsでdirectory symlinkを作れない環境向け1件のみskip（2026-08-05）。`git diff --check`、Node構文検査、PowerShell parser、通常起動相当`setup_comfy.ps1 -VerifyOnly -SkipModelHash`も成功。この件数は削除済み旧経路のテストを含むため、現行HEADのtest collection件数とは扱わない
- cleanup後公開監査（現行）: staged Git blob 43ファイル、合計690833 bytes。10MiB超ファイル、モデル／動画／画像／音声binary、symlink、既知形式のtoken／private key、開発者固有absolute pathはいずれも0件。旧実装の実行コード、専用requirements、lock、custom node、対応テストは配布物から削除済み
- cleanup後unit tests（現行）: 133件中132件成功、Windowsでdirectory symlinkを作れない環境向け1件のみskip（2026-08-05）。staged indexだけを別の新規Git repositoryへ展開したfresh-checkout相当でも同じ133件が成功。`git diff --check`、Node構文検査、全Python compile、全PowerShell parser、通常起動相当`setup_comfy.ps1 -VerifyOnly -SkipModelHash`も成功

# AiCostTray-pricing

AiCostTrayが使う単価データ (pricing.json) の配信用リポジトリです。
アプリは `main` ブランチの `pricing.json` を 12 時間ごとに取りに行き、今より新しい版なら取り込みます。

```
https://raw.githubusercontent.com/Inhouse-erp/AiCostTray-pricing/main/pricing.json
```

## 形式

- 単位は USD / 100 万トークン。モデルID の最長前方一致で適用する
- `schema`: ファイルの形式。今は `1`。アプリが対応していない形式の版は取り込まれない
- `version`: 単価データの版。日付 8 桁 + 連番 2 桁の整数 (例: `2026101101`)。日付は実在する日で、今日から 3 日より先にしない (先の日付の版を一度取り込んだアプリは、それより小さい正しい版を二度と取り込めなくなるため、アプリも CI も弾く)
- `models`: モデルID ごとの単価 (`input` / `output` / `cacheWrite5m` / `cacheWrite1h` / `cacheRead`)。長いコンテキストの割増があるモデルは `longContext` (`threshold` と各単価) を持つ

## LiteLLM との自動同期

`.github/workflows/sync-litellm.yml` が毎週月曜 9:00 (日本時間) に、[LiteLLM](https://github.com/BerriAI/litellm) の単価表 (`model_prices_and_context_window.json`) と比べます。差分があれば、`sync/litellm` ブランチに `pricing.json` を直したコミットを作って PR を出します。マージは人がします。Actions の画面から手動でも実行できます (sync-litellm → Run workflow)。

- 変換は `scripts/sync_litellm.py` が行う。LLM は使わない
  - 単価は 1 トークンあたりから 100 万トークンあたりに直す
  - モデルID は、GPT 系の `gpt-5.4` を `gpt-5-4` に読み替える
  - LiteLLM の `cache_creation_input_token_cost_above_1hr` をキャッシュ書込 1h にする。無いモデル (OpenAI など) は、今の値が 5m と同じなら 5m に合わせる
  - 長文脈の割増は `..._above_272k_tokens` などから取る
- pricing.json にあるモデル: LiteLLM と値が違えば LiteLLM の値にする。LiteLLM に無い項目・モデル (引退した古いモデルなど) は今の値のまま
- LiteLLM にあって pricing.json に無い Claude / GPT のモデルは追加する。要らないものは PR から行を消し、`sync-ignore.txt` に正規表現を足す (次回から追加しない)。古い世代 (gpt-3 / gpt-4) と、音声・検索・画像のモデルは最初から除いている
- 差分があれば `version` を上げる (日本時間の日付 + 連番)
- PR は毎週作り直す (`sync/litellm` ブランチを強制 push)。PR に手を入れたら、次の実行の前にマージする
- Actions のトークンで作った PR では pull_request のワークフローが走らないので、同期のワークフローが validate を起動する
- 組織の設定で Actions に PR の作成が許されていないときは、ブランチだけ push して Issue (ラベル `sync-litellm`) で知らせる。Issue のリンクから PR を作る
- 手元で試す:

  ```bash
  curl -sL https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json -o litellm.json
  python scripts/sync_litellm.py litellm.json pricing.json
  ```

## 単価を手で更新する手順

1. ブランチを切って `pricing.json` を直す
2. `version` を必ず上げる。上げないとアプリは取り込まない
3. 手元で確かめる

   ```bash
   python scripts/validate.py pricing.json
   ```

4. PR を出す。GitHub Actions (`.github/workflows/validate.yml`) が形式と値の範囲を確かめ、マージ先の版と比べる
5. マージすると、各利用者のアプリが次の確認 (最長 12 時間後) で取り込む。料金表の画面の「配信版の単価を取り込む」ですぐに取り込むこともできる
6. 次にアプリを配布するときは、本体リポジトリの `pricing.json` (exe に同梱する初期単価) も同じ内容にそろえる

## アプリが取り込まないもの

このリポジトリが消えた・壊れた・書き換えられた場合でも、利用者のアプリは今の単価のまま動き続けます。次の版は丸ごと取り込みません (一部だけ取り込むことはしない)。

- 取得できない (リポジトリが消えた、非公開になった、ネットワークにつながらない)
- JSON として読めない、512 KB を超える
- `schema` に対応していない、`version` が不正 (実在しない日付、今日から 3 日より先の日付を含む)、モデルが 5 件未満
- モデルID に使えない文字がある、単価が 0 未満か 1000 を超える、長文脈の閾値が不正
- 今は有料の入力・出力の単価が 0 になっている (コストと予算の通知を黙らせる書き換えを防ぐため)
- 今の表にあるモデルの単価が、今の値から 10 倍を超えて上がる・下がる (新しいモデルID は倍率を比べない。上位版・下位版は親のモデルID の 10 倍以上違うのが普通なため。0 になる判定は、新しいモデルID が前方一致で既存の単価を横取りする場合にも行う)
- `version` が今の表の版以下 (古い単価で新しい単価を戻さないため)

また、アプリはモデルを消しません。このリポジトリからモデルを消しても、取り込み済みの利用者の表には残ります。

## 運用上の注意

- `main` にはブランチ保護をかけ、PR とチェックの成功を必須にしてください (Settings → Branches)
- 単価の値下げなど 10 倍を超える正当な変更は、アプリに取り込まれません。その場合は本体の同梱版で配布してください
- 同期のワークフローに PR を作らせるには、Settings → Actions → General → Workflow permissions の「Allow GitHub Actions to create and approve pull requests」をオンにする。組織 (Inhouse-erp) 側で禁止されていると選べないので、組織の管理者に組織の同じ設定をオンにしてもらう。オンにできなくても、Issue で知らせる形で動く

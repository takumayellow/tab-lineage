# tab-lineage

Chromium 系ブラウザ（Vivaldi / Chrome / Edge / Brave）の閲覧履歴から，**どのページからどこへ脱線したか**が分かる系統樹を作り，1 枚の HTML で見られるようにするツール．

- 訪問を「どこから開いたか」でつなぎ，同じサイト内の移動や戻る・再読み込みを畳んで，読める大きさの木にする．
- 無操作の時間で「回」に区切り，回の中を話題ごとの「スレッド」に分け，本筋から外れた枝を「寄り道」として畳む．
- 開いているタブ（セッションファイル）を読んで，重複・検索結果・放置したタブを見分けた整理案を出す．
- 出力は外部への通信を持たない 1 枚の HTML．書体だけ Google Fonts から読む．
- 整理案を Vivaldi へ戻せる（ワークスペース・名前付きのタブスタック・あとで読むのブックマーク）．

履歴 DB は読み取り専用で開き，解析はコピーに対して行う．ブラウザに手を入れるのは `apply` だけで，それも足すだけ（タブ・ワークスペース・ブックマークを閉じない・消さない）．

## 使い方

Python 3.11 以上．依存パッケージは無い（`apply` だけ websocket-client を使う）．

```sh
pip install git+https://github.com/takumayellow/tab-lineage
```

1. **履歴をコピーする**．ブラウザは DB をロックしているので，使用中でも読めるようにコピーを取る．

   ```sh
   tab-lineage snapshot data/2026-09-30              # 既定は Vivaldi の Default プロファイル
   tab-lineage snapshot data/2026-09-30 --browser chrome --profile "Profile 1"
   ```

   `History.db` と，読めるうち一番新しい `Session_*` が入る．

2. **回の一覧を見る**．

   ```sh
   tab-lineage outline data/2026-09-30/History.db --top 20
   ```

   回の ID（開始時刻）と，回の中のスレッドの ID（根の訪問 ID）が出る．

3. **必要なら名前を付ける**．自動の名前はページの題名から取るので，公開する前に確かめる．[`examples/labels.toml`](examples/labels.toml) の形で，回に名前・メモを付けたり，隠したりできる．

4. **サイトを作る**．

   ```sh
   tab-lineage build data/2026-09-30/History.db \
     --session data/2026-09-30/Session_13400000000000000 \
     --config my.toml --labels labels.toml --out site/index.html
   ```

   日付の違うコピーを複数渡すと訪問 ID で重ね合わせる．History は古い訪問から消えていくので，定期的にコピーしておくと長い期間を扱える．

5. **タブを並べ直す**（Vivaldi）．今のタブを，ワークスペース → スタック → タブと，あとで読むのブックマークの木に分けた案を JSON に出す．

   ```sh
   tab-lineage arrange data/2026-09-30/History.db      --session data/2026-09-30/Session_13400000000000000      --config my.toml --out data/arrange.json
   ```

   JSON は手で直してよい（スタックの名前，ブックマークのフォルダ，タブの移動）．ワークスペースの絵文字，読みもののワークスペース，ブックマークのフォルダ分けは設定の `[arrange]` で決める．
   出力には URL と題名がそのまま入るので，リポジトリやサイトに載せない．

   起動中の Vivaldi に適用する．

   ```sh
   pip install "tab-lineage[apply] @ git+https://github.com/takumayellow/tab-lineage"
   tab-lineage apply data/arrange.json --dry-run      # 木を表示するだけ
   tab-lineage apply data/arrange.json --port 9222
   ```

   `apply` は DevTools プロトコルで Vivaldi の画面に接続する．Vivaldi を `--remote-debugging-port=9222` で起動しておく．Chromium 136 以降，既定のユーザーデータのフォルダではこのフラグが無視されるので，`--user-data-dir` で別のフォルダを指定して起動したものにしか適用できない．
   ポートが開いている間は，この PC のどのプログラムもブラウザを操作でき，Cookie や保存したパスワードも読める．開けっぱなしにせず，適用が済んだらフラグ無しで起動し直す．
   適用すると，案のワークスペースを名前で探して無ければ作り，最後に使ったウィンドウにタブを作ってスタックにまとめ，ブックマークバーにフォルダを足す．同じワークスペースに同じ URL のタブや，同じフォルダに同じ URL のブックマークがあれば足さないので，適用し直しても増えない．最初のワークスペースを開いたあと，ほかのワークスペースに作ったタブは休止させる（隠れたワークスペースのタブも休止させるまではメモリを使う）．

ほかのコマンド: `stats`（集計），`tabs`（セッションファイルのタブを並び順に表示）．`tab-lineage <command> -h` で引数を表示する．

## 画面

| 画面 | 内容 |
|---|---|
| 概観 | 日 × 24 時間の帯で回を並べる．大きい回と名前を付けた回を上に出す |
| 回の一覧 | 語・ワークスペースで絞り込み，新しい順・大きい順などで並べる |
| 回 | スレッドごとのレーンに訪問を時刻で打ち，下に木を出す．寄り道の枝は畳んである |
| 今のタブ | 開いているタブをワークスペース → 目的 → タブの木に並べた整理案 |
| 規則 | 木と回を作った規則と，その回の集計 |

## プライバシー

出力は閲覧履歴そのものなので，既定でも次のものは載せない．設定で足せる．

- **URL のクエリとフラグメント**．検索語やセッション ID が入りやすいので常に落とす（`keep_query = true` で残す）．
- **パスに入ったトークン・文書 ID・共有リンクの ID・メールアドレス**．英大小文字と数字の混じった長い区切り，英小文字と数字だけの長い区切り，長い 16 進を `…` に置き換える．
- **ログイン・決済の画面**（`privacy.drop`）．その訪問を消し，子は親につなぎ直す．
- **メール・DM・地図**（`privacy.mask`）．題名とパスを伏せ，「メール」のような名前だけ残す．地図は店や住所から住んでいる場所が分かるので既定で伏せる．パターンの host を `*.example.com` のように書くと，サブドメイン（取引先名や案件名が入りやすい）も落として `example.com` だけ残す．
- **中身の無い中継画面**（`privacy.drop_titles`）．
- **検索語**のうち，メールアドレス・電話番号らしい数字の並び・URL・長すぎるもの．`privacy.drop_terms` で足せる．
- ローカルファイルはファイル名だけ残す．

自動で付く回やスレッドの名前はページの題名から取る．SNS の投稿の題名には本文や他人の名前が入るので，公開するならそのサイトを `mask` するか，`labels.toml` で名前を付け直すか隠す．
**生の History や Session ファイルはリポジトリに入れない**（`.gitignore` 済み）．

設定の全項目と既定値は [`src/tab_lineage/data/default.toml`](src/tab_lineage/data/default.toml)，例は [`examples/config.toml`](examples/config.toml)．

## 仕組み

履歴 DB とセッションファイルの読み方，木と回を作る規則は [`docs/know-how.md`](docs/know-how.md)．

## 開発

```sh
pip install -e ".[test]"
pytest
```

テストは合成した DB とセッションファイルだけを使う．

## ライセンス

MIT

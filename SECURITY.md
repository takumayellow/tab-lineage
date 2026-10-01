# セキュリティ

脆弱性は公開の Issue に書かず，GitHub の Security タブにある「Report a vulnerability」（非公開の報告）から知らせてください．

とくに次のものは脆弱性として扱います．

- 生成したサイト（`build`）に，消す・伏せる設定のページの URL・題名・検索語が残る
- `apply` が http・https・`file:///` 以外の URL を開く，または 127.0.0.1 以外の DevTools に接続する
- 履歴やセッションのファイルを読み取り以外で開く

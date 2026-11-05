# Registry ポータルの開発環境を準備する

コマンドは、特記がなければリポジトリのルートで実行する。

## ランタイムとキャッシュを設定する

バックエンドには Python 3.13 を使う。Node は 24.15.0 以上の24系を使う。context-mode の補助フックも同じ Node を使う必要がある。

nvm を使う場合は、新しいターミナルで次を実行する。

```sh
nvm install 24
nvm use 24
node --version
```

Orca をすでに起動している場合は、この PATH が新しいエージェントの起動環境にも反映されているか確認する。context-mode の `ctx_doctor` で Node とフックの診断を行う。フックが失敗する場合はエラーを保存し、フックを無効化して回避せず、起動環境を修正する。

```sh
. applications/registry_portal/scripts/dev-env.sh
```

`gh` の認証は既存の設定を利用する。`gh auth status` の結果で認証の成否を確認し、設定ファイルの読み取り拒否と API への接続失敗を区別する。接続失敗だけを理由にファイルの許可範囲を広げない。認証情報をリポジトリへコピーしない。

## コミット前の検査を有効にする

先に [アプリの開発手順](../../applications/registry_portal/README.md#開発) に従い、バックエンドの `.venv` とフロントエンドの依存パッケージを準備する。

```sh
git config core.hooksPath .githooks
applications/registry_portal/backend/.venv/bin/pre-commit run --all-files
```

共有フック `.githooks/pre-commit` はキャッシュを設定して pre-commit を起動する。変更対象に応じて Registry ポータルの Black、Flake8、import 境界、ESLint を実行する。既存の仮想環境と npm の固定依存を使う。テスト・型検査・ビルドは README のコマンドと CI で確認する。

## Git 操作の結果を確認する

着手前に現在のブランチ、未コミット変更、既存 worktree を確認する。

```sh
git status --short --branch
git worktree list --porcelain
```

Orca 管理の worktree は Orca の CLI で作成・整理する。作成先は実際に書き込みできる場所を選ぶ。手動で作る場合は、無視対象の `.worktrees/` を使う。使用中のブランチを別の worktree へ重ねてチェックアウトしない。

`git switch`、`git worktree`、`git commit`、`git push` は単独で実行し、標準出力・標準エラーと終了コードを確認する。パイプで `tail` や `head` に渡すと元の終了コードを見落とし得る。ブランチを切り替えた後は、次を実行して実際の状態を確認する。

```sh
git branch --show-current
git status --short --branch
```

操作が失敗した場合は状態を確認してから復旧する。HEAD の直接変更や強制的なチェックアウトで未コミット変更を上書きしない。worktree の削除前は、その場所の未コミット変更と作業中のプロセスを確認する。

## 調査出力を必要な範囲に絞る

コードの構造調査は codebase-memory のスキルに従う。設定や文字列の確認は `rg` と対象行の読み取りを使う。大量のログはファイルへ保存し、終了コードを保持して必要な部分を取り出す。状態を変更するコマンドの出力は省略しない。

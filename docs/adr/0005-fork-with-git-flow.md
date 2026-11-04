# 本家と分けて fork 上で育て、ブランチは Git Flow 型で運用する

このリポジトリは `aws-samples/sample-agentic-value-accelerator` の fork である。Registry ポータルや日本語文書などの独自の成果は本家に送らず、fork の `main` で管理する。本家の `main` は定期的に取り込み、本家への還元は fork 独自の内容を含まない汎用的な修正に限って個別に行う。ブランチは `main`（リリース済み）と `develop`（統合）を常設する Git Flow 型とし、作業ブランチの PR は `develop` に出す。詳細は `docs/branch-strategy-ja.md` に定める。

本家の手順（`upstream/main` に rebase して本家の `main` へ PR）に合わせる案もあったが、本家にない独自の成果を継続して積み上げるため採らなかった。GitHub Flow（`main` だけ）より手順は増えるが、デプロイが手動であり（ADR-0004）、`main` を「反映してよい状態」として区別しておくことを優先した。

## Consequences

- fork の既定ブランチを `develop` にし、`main` と `develop` の直接 push を禁止する。
- `develop` から `main` へのマージは merge commit で行い、両者の履歴を分岐させない。
- 本家の取り込みは merge で行い、共有済みのブランチを rebase しない。
- 本家のリリース番号と混同しないよう、タグはアプリ単位（例: `registry-portal/v0.1.0`）で付ける。
- `.github/CONTRIBUTING.md` と `.github/CODEOWNERS` は本家の運用のままにしており、fork では本家へ還元するときだけ適用する。

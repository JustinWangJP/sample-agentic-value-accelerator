# MVP1 は公開 HTTPS で提供し、閉域化と private 用 Registry は見送る

MVP1 は us-east-1 で、CloudFront（非公開 S3 + OAC）、API Gateway HTTP API、VPC 外の Lambda で構成する。WAF、VPC、NAT、独自ドメインは使わない。防御は、JWT authorizer と、Lambda 内でのロール・所有者の検査に頼る。Registry は既存の1つだけを使う。Registry の検索と一覧の認可は Registry 単位であり、レコード単位では見える範囲を制限できない。そのため、データ分類（public / private）は表示用のラベルとして扱い、可視性の制御には使わない。confidential 相当のレコードは登録時に拒否する。

## Consequences

- WAF が必須になった場合は、HTTP API を REST API に移行するか、API を CloudFront の背後に置く構成変更が必要になる。SPA 用の CloudFront に WAF を付けても、API は保護されない。
- 所有チームだけが発見できる private な Registry レコードが必要になった場合は、MVP2 で別の Registry を追加して対応する。
- 検索処理は、同じ地理区分内（米国）の別リージョンで行われる可能性がある。

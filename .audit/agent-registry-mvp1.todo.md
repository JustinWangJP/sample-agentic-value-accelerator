# AWS Agent Registry MVP1 設計調査

- [x] 1. Route through the **how** skill. For motivation questions, also route through the **why** skill.
  - skip: why は過去の判断理由を問う調査ではないため適用しない。
- [x] 2. Throughput checkpoint stays one line: `throughput checkpoint: n/a, read-only investigation`.
- [x] 3. Produce the `how`-shaped output (Overview / Key Concepts / How It Works / Where Things Live / Gotchas), or a recommendation with a tradeoffs table if the request is a decision between alternatives.
- [x] 4. Apply the **unslop** skill to the reply.

throughput checkpoint: n/a, read-only investigation

- [x] 現状実装と再利用できる境界を確認する。
- [x] AWS MCP と公式 API 仕様で承認・JWT・SDK の条件を確認する。
- [x] MVP1 の機能・認可・最小インフラ・受入条件を文書に残す。
- [x] MVP2 の AgentCore 連携との境界を整理する。

成果物は plan/aws-agent-registry-mvp1-design-ja.md。文書内のローカル参照13件の存在を確認した。アプリケーション実装、実AWS接続、デプロイは今回の調査範囲に含めない。

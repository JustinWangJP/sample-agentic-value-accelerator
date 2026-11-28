/**
 * Registry ポータル 静的 HTML プロトタイプの定義・サンプルデータ・判定関数。
 * DOM に依存しないため、ブラウザでは window.RegistryPrototypeModel として、
 * Node のテスト（tests/）では vm で読み込んで同じ定義を検証する。
 *
 * 種別・descriptor・データ分類・schema 版・操作種別・通知種別の値とラベルは、
 * 本番フロントエンド（frontend/src/）の定義の写しをこのファイル一か所に置く。
 * 写しが本番とずれていないことは tests/model.test.mjs が照合する。
 */
(function (global) {
  'use strict';

  /** 業務ロール名（Cognito グループ名）。プロトタイプ内のロール指定はすべてこの値を使う */
  const ROLES = Object.freeze({
    VIEWER: 'Viewer',
    PUBLISHER: 'Publisher',
    CURATOR: 'Curator',
    ADMIN: 'Admin',
  });
  const ROLE_NAMES = Object.freeze(Object.values(ROLES));

  /** プロトタイプ確認用バーで切り替える業務ロールの組み合わせ */
  const ROLE_PRESETS = Object.freeze({
    all: {
      label: '全ロール',
      roles: [ROLES.VIEWER, ROLES.PUBLISHER, ROLES.CURATOR, ROLES.ADMIN],
      teams: ['team-platform', 'team-fsi-core'],
    },
    viewer: { label: ROLES.VIEWER, roles: [ROLES.VIEWER], teams: [] },
    publisher: {
      label: ROLES.PUBLISHER,
      roles: [ROLES.VIEWER, ROLES.PUBLISHER],
      teams: ['team-platform', 'team-fsi-core'],
    },
    curator: {
      label: ROLES.CURATOR,
      roles: [ROLES.VIEWER, ROLES.CURATOR],
      teams: ['team-governance'],
    },
    admin: {
      label: ROLES.ADMIN,
      roles: [ROLES.VIEWER, ROLES.ADMIN],
      teams: ['team-platform-admin'],
    },
  });
  const DEFAULT_ROLE_PRESET = 'all';

  /** 左サイドバーとモバイルメニューの項目。requiredRoles が空なら全員に表示する */
  const NAV_SECTIONS = Object.freeze([
    {
      title: '発見',
      requiredRoles: [],
      items: [{ key: 'catalog', href: './index.html', label: 'カタログ' }],
    },
    {
      title: '登録（Publisher）',
      requiredRoles: [ROLES.PUBLISHER],
      items: [{ key: 'drafts', href: './drafts.html', label: '下書き・申請' }],
    },
    {
      title: '審査・公開（Curator）',
      requiredRoles: [ROLES.CURATOR],
      items: [
        { key: 'reviews', href: './reviews.html', label: '審査待ち' },
        { key: 'publication', href: './publication.html', label: '公開管理' },
      ],
    },
    {
      title: '管理（Admin）',
      requiredRoles: [ROLES.ADMIN],
      items: [
        { key: 'administration', href: './administration.html', label: 'Registry 管理' },
        { key: 'recovery', href: './recovery.html', label: '回復操作' },
      ],
    },
  ]);

  /** サイドバーとモバイルメニューの末尾に置く、プロトタイプ参照用のリンク */
  const STATES_NAV_ITEM = Object.freeze({
    sectionTitle: 'プロトタイプ参照',
    href: './states.html',
    label: '状態・デザイン一覧',
  });

  // ---- 本番フロントエンドの定義の写し（tests/model.test.mjs で照合） ----

  /** frontend/src/api/recordType.ts の CATALOG_RECORD_TYPES と RECORD_TYPE_LABELS */
  const RECORD_TYPES = Object.freeze([
    { value: 'AGENT', label: 'Agent' },
    { value: 'MCP', label: 'MCP サーバー' },
    { value: 'SKILL', label: 'Skill' },
  ]);

  /** frontend/src/drafts/descriptors.ts の DESCRIPTOR_TYPES と DESCRIPTOR_LABELS */
  const DESCRIPTOR_TYPES = Object.freeze([
    { value: 'custom', label: 'custom（フォームで入力）' },
    { value: 'a2aAgentCard', label: 'A2A（Agent Card）' },
    { value: 'mcpServer', label: 'MCP サーバー（server 定義と tools 定義）' },
    { value: 'agentSkillsDefinition', label: 'Skill（SKILL.md と配布物の参照）' },
  ]);

  /**
   * frontend/src/api/dataClassification.ts の DATA_CLASSIFICATIONS。
   * 本番には表示ラベルの定義がないため、括弧内の補足はプロトタイプで提案する表示。
   */
  const DATA_CLASSIFICATIONS = Object.freeze([
    { value: 'public', label: 'public（組織外公開可）' },
    { value: 'private', label: 'private（組織内限定）' },
  ]);

  /** frontend/src/drafts/descriptors.ts の各 *_SCHEMA_VERSIONS */
  const SCHEMA_VERSIONS = Object.freeze({
    a2a: ['0.3'],
    mcpServer: ['2025-12-11', '2025-10-17', '2025-10-11', '2025-09-29', '2025-09-16', '2025-07-09'],
    mcpTools: ['2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'],
    skill: ['0.1.0'],
  });

  /** frontend/src/drafts/api.ts の KIND_LABELS のうち、プロトタイプの履歴で使うもの */
  const HISTORY_KIND_LABELS = Object.freeze({
    UPDATE: '編集',
    SUBMIT: '申請',
    APPROVE: '公開承認',
    REQUEST_DEPRECATION: '廃止依頼',
  });

  /** frontend/src/drafts/api.ts の STATE_LABELS のうち、プロトタイプの履歴で使うもの */
  const HISTORY_STATE_LABELS = Object.freeze({
    SUCCEEDED: '成功',
  });

  /** frontend/src/notifications/api.ts の NOTIFICATION_KIND_LABELS のうち、プロトタイプで使うもの */
  const NOTIFICATION_KIND_LABELS = Object.freeze({
    SUBMITTED: '公開申請',
    APPROVED: '公開承認',
    OUTSIDE_CHANGE: 'ポータル外の変更',
  });

  /** select 要素の data-options 属性から参照する選択肢の集合 */
  const toOptions = (values) => values.map((value) => ({ value, label: value }));
  const OPTION_SETS = Object.freeze({
    recordTypes: RECORD_TYPES,
    descriptorTypes: DESCRIPTOR_TYPES,
    dataClassifications: DATA_CLASSIFICATIONS,
    a2aSchemaVersions: toOptions(SCHEMA_VERSIONS.a2a),
    mcpServerSchemaVersions: toOptions(SCHEMA_VERSIONS.mcpServer),
    mcpToolsSchemaVersions: toOptions(SCHEMA_VERSIONS.mcpTools),
    skillSchemaVersions: toOptions(SCHEMA_VERSIONS.skill),
  });

  // ---- 表示状態（CODING_STANDARDS.md の「0件・未取得・準備中・失敗」の区別） ----

  /**
   * プロトタイプ確認用バーで切り替える表示状態。
   * 一覧領域（data-state-region）は loading / empty / preparing / error* を、
   * 項目単位の値（data-state-only / data-state-hide）は notFetched を表現する。
   */
  const VIEW_STATES = Object.freeze([
    { key: 'normal', label: '正常' },
    { key: 'loading', label: '読み込み中' },
    { key: 'empty', label: '0 件' },
    { key: 'notFetched', label: '未取得' },
    { key: 'preparing', label: '準備中' },
    { key: 'error401', label: '失敗 401' },
    { key: 'error403', label: '失敗 403' },
    { key: 'error5xx', label: '失敗 5xx' },
  ]);
  const DEFAULT_VIEW_STATE = 'normal';

  /** 一覧領域が既定で表現する表示状態（準備中は A2A 検索の結果領域だけが明示的に指定する） */
  const DEFAULT_REGION_STATES = Object.freeze(['loading', 'empty', 'error401', 'error403', 'error5xx']);

  // ---- サンプルデータ ----

  /** 通知ポップオーバーのサンプル（GET /api/notifications の応答例） */
  const NOTIFICATION_SAMPLES = Object.freeze([
    {
      kind: 'SUBMITTED',
      unread: true,
      at: '12分前',
      title: '引受審査トリアージ Agent（v1.2.0）',
      body: 'team-fsi-core から公開承認の申請が届きました。',
    },
    {
      kind: 'OUTSIDE_CHANGE',
      unread: true,
      at: '2時間前',
      title: 'legacy-billing-mcp（rec881029384）',
      body: 'ポータルの操作記録と一致しない状態変化を検知しました。',
    },
    {
      kind: 'APPROVED',
      unread: false,
      at: '昨日 16:40',
      title: '社内規定検索 MCP サーバー（v2.0.1）',
      body: '公開承認が完了し、カタログ項目として発見可能になりました。',
    },
  ]);

  /** 操作と審査の履歴ドロワーのサンプル（GET /api/records/{recordId}/history の応答例） */
  const HISTORY_SAMPLES = Object.freeze({
    'catalog-agent': {
      subtitle: 'rec102938475（underwriting-triage-agent）',
      entries: [
        {
          kind: 'APPROVE',
          state: 'SUCCEEDED',
          at: '2026-11-20 14:32:10',
          reason: 'セキュリティレビューおよび接続先確認完了。',
          details: [
            '版: v1.1.0 / 操作ID: op_01JD8K9M...',
            '主体: sub=curator-001 / AWS RequestId: req-99a8b7',
            '対象ハッシュ: sha256:8f3c...e21a',
          ],
        },
        {
          kind: 'SUBMIT',
          state: 'SUCCEEDED',
          at: '2026-11-20 10:15:04',
          reason: 'A2A の Agent Card のスキル説明を更新し v1.1.0 を申請。',
          details: ['版: v1.1.0 / 操作ID: op_01JD7Z2X...', '主体: sub=publisher-042'],
        },
        {
          kind: 'UPDATE',
          state: 'SUCCEEDED',
          at: '2026-11-19 18:02:44',
          recovery: {
            title: 'Admin 回復操作の記録（RECONCILED）',
            lines: [
              '方法: Registry 再取得照合で確定 / 主体: sub=admin-001',
              '理由: タイムアウト後に Registry 反映済みを確認して確定。',
            ],
          },
        },
      ],
    },
    'draft-edit': {
      subtitle: 'rec102938475（underwriting-triage-agent）',
      entries: [
        {
          kind: 'UPDATE',
          state: 'SUCCEEDED',
          at: '2026-11-27 15:10',
          details: ['版: v1.2.0 / 主体: sub=publisher-042'],
        },
        {
          kind: 'APPROVE',
          state: 'SUCCEEDED',
          at: '2026-11-20 14:32',
          details: ['版: v1.1.0 / 主体: sub=curator-001'],
        },
      ],
    },
    'review-detail': {
      subtitle: 'rec102938475（underwriting-triage-agent）',
      entries: [
        {
          kind: 'SUBMIT',
          state: 'SUCCEEDED',
          at: '2026-11-28 09:40',
          details: ['版: v1.2.0 / 主体: sub=publisher-042'],
        },
        {
          kind: 'APPROVE',
          state: 'SUCCEEDED',
          at: '2026-11-20 14:32',
          details: ['版: v1.1.0 / 主体: sub=curator-001'],
        },
      ],
    },
    publication: {
      subtitle: 'legacy-contract-mcp',
      entries: [
        {
          kind: 'REQUEST_DEPRECATION',
          state: 'SUCCEEDED',
          at: '2026-11-23 11:20',
          details: ['主体: sub=publisher-019'],
        },
      ],
    },
  });

  // ---- 判定関数（DOM に依存しない） ----

  /** data-requires-roles・data-state-kinds などの空白区切りの指定を配列にする */
  function parseTokenList(value) {
    return String(value || '')
      .split(/\s+/)
      .filter(Boolean);
  }

  /** ROLES に定義されていないロール名を返す（HTML の指定ミスの検出用） */
  function findUnknownRoles(roles) {
    return roles.filter((role) => !ROLE_NAMES.includes(role));
  }

  /** 必要なロールのいずれかを持つか。必要なロールの指定がなければ常に許可する */
  function isAllowedForRoles(requiredRoles, grantedRoles) {
    return requiredRoles.length === 0 || requiredRoles.some((role) => grantedRoles.includes(role));
  }

  function resolveRolePreset(key) {
    return Object.prototype.hasOwnProperty.call(ROLE_PRESETS, key) ? key : DEFAULT_ROLE_PRESET;
  }

  function resolveViewState(key) {
    return VIEW_STATES.some((state) => state.key === key) ? key : DEFAULT_VIEW_STATE;
  }

  /** 一覧領域が指定の表示状態を表現するか。kinds が空なら既定の表示状態を使う */
  function regionShowsState(regionStates, viewState) {
    const kinds = regionStates.length > 0 ? regionStates : DEFAULT_REGION_STATES;
    return kinds.includes(viewState);
  }

  /** 廃止の二重確認: 確認チェックがあり、入力したレコード名が一致するときだけ実行できる */
  function isDeprecationConfirmed({ checked, typedName, expectedName }) {
    return Boolean(checked) && expectedName !== '' && String(typedName).trim() === expectedName;
  }

  global.RegistryPrototypeModel = Object.freeze({
    ROLES,
    ROLE_NAMES,
    ROLE_PRESETS,
    DEFAULT_ROLE_PRESET,
    NAV_SECTIONS,
    STATES_NAV_ITEM,
    RECORD_TYPES,
    DESCRIPTOR_TYPES,
    DATA_CLASSIFICATIONS,
    SCHEMA_VERSIONS,
    HISTORY_KIND_LABELS,
    HISTORY_STATE_LABELS,
    NOTIFICATION_KIND_LABELS,
    OPTION_SETS,
    VIEW_STATES,
    DEFAULT_VIEW_STATE,
    DEFAULT_REGION_STATES,
    NOTIFICATION_SAMPLES,
    HISTORY_SAMPLES,
    parseTokenList,
    findUnknownRoles,
    isAllowedForRoles,
    resolveRolePreset,
    resolveViewState,
    regionShowsState,
    isDeprecationConfirmed,
  });
})(globalThis);

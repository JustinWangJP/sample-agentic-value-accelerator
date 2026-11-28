// prototype-model.js の定義と判定関数のテスト。
// 本番フロントエンドの定義の写しがずれていないことを照合する（CODING_STANDARDS.md「種別やデータ分類の値は一か所に定義する」）。
import assert from 'node:assert/strict';
import path from 'node:path';
import { describe, it } from 'node:test';

import {
  FRONTEND_SRC_DIR,
  extractStringArray,
  extractStringRecord,
  loadModel,
  readText,
} from './helpers.mjs';

const model = loadModel();
const frontend = (relativePath) => readText(path.join(FRONTEND_SRC_DIR, relativePath));
const values = (options) => options.map((option) => option.value);
const labels = (options) => Object.fromEntries(options.map((option) => [option.value, option.label]));

/** プロトタイプの部分集合のラベルが、本番の対応表と一致することを確かめる */
function assertSubsetOf(prototypeLabels, frontendLabels) {
  for (const [key, label] of Object.entries(prototypeLabels)) {
    assert.equal(frontendLabels[key], label, `${key} のラベルが本番と異なります`);
  }
}

describe('本番フロントエンドの定義との照合', () => {
  it('種別（recordType）の値とラベルが一致する', () => {
    const source = frontend('api/recordType.ts');
    assert.deepEqual(values(model.RECORD_TYPES), extractStringArray(source, 'CATALOG_RECORD_TYPES'));
    assert.deepEqual(labels(model.RECORD_TYPES), extractStringRecord(source, 'RECORD_TYPE_LABELS'));
  });

  it('descriptor 種別の値とラベルが一致する', () => {
    const source = frontend('drafts/descriptors.ts');
    assert.deepEqual(values(model.DESCRIPTOR_TYPES), extractStringArray(source, 'DESCRIPTOR_TYPES'));
    assert.deepEqual(labels(model.DESCRIPTOR_TYPES), extractStringRecord(source, 'DESCRIPTOR_LABELS'));
  });

  it('データ分類の値と表示ラベルが一致する', () => {
    const source = frontend('api/dataClassification.ts');
    assert.deepEqual(
      values(model.DATA_CLASSIFICATIONS),
      extractStringArray(source, 'DATA_CLASSIFICATIONS'),
    );
    assert.deepEqual(
      labels(model.DATA_CLASSIFICATIONS),
      extractStringRecord(source, 'DATA_CLASSIFICATION_LABELS'),
    );
  });

  it('descriptor の schema 版が一致する', () => {
    const source = frontend('drafts/descriptors.ts');
    assert.deepEqual(model.SCHEMA_VERSIONS.a2a, extractStringArray(source, 'A2A_SCHEMA_VERSIONS'));
    assert.deepEqual(
      model.SCHEMA_VERSIONS.mcpServer,
      extractStringArray(source, 'MCP_SERVER_SCHEMA_VERSIONS'),
    );
    assert.deepEqual(
      model.SCHEMA_VERSIONS.mcpTools,
      extractStringArray(source, 'MCP_TOOLS_SCHEMA_VERSIONS'),
    );
    assert.deepEqual(model.SCHEMA_VERSIONS.skill, extractStringArray(source, 'SKILL_SCHEMA_VERSIONS'));
  });

  it('履歴の操作種別・結果と通知種別のラベルが一致する', () => {
    const draftsApi = frontend('drafts/api.ts');
    assertSubsetOf(model.HISTORY_KIND_LABELS, extractStringRecord(draftsApi, 'KIND_LABELS'));
    assertSubsetOf(model.HISTORY_STATE_LABELS, extractStringRecord(draftsApi, 'STATE_LABELS'));
    assertSubsetOf(
      model.NOTIFICATION_KIND_LABELS,
      extractStringRecord(frontend('notifications/api.ts'), 'NOTIFICATION_KIND_LABELS'),
    );
  });
});

describe('定義の内部整合性', () => {
  it('ナビとロール切替が ROLES の値だけを使う', () => {
    const used = [
      ...model.NAV_SECTIONS.flatMap((section) => section.requiredRoles),
      ...Object.values(model.ROLE_PRESETS).flatMap((preset) => preset.roles),
    ];
    assert.deepEqual(model.findUnknownRoles(used), []);
  });

  it('サンプルデータの種別にはすべてラベルがある', () => {
    for (const item of model.NOTIFICATION_SAMPLES) {
      assert.ok(model.NOTIFICATION_KIND_LABELS[item.kind], `通知種別 ${item.kind} のラベルがありません`);
    }
    for (const sample of Object.values(model.HISTORY_SAMPLES)) {
      for (const entry of sample.entries) {
        assert.ok(model.HISTORY_KIND_LABELS[entry.kind], `操作種別 ${entry.kind} のラベルがありません`);
        assert.ok(model.HISTORY_STATE_LABELS[entry.state], `結果 ${entry.state} のラベルがありません`);
      }
    }
  });
});

describe('業務ロールの判定', () => {
  it('必要なロールの指定がなければ常に許可する', () => {
    assert.equal(model.isAllowedForRoles([], []), true);
  });

  it('必要なロールのいずれかを持てば許可し、持たなければ拒否する', () => {
    assert.equal(model.isAllowedForRoles(['Curator', 'Admin'], ['Viewer', 'Admin']), true);
    assert.equal(model.isAllowedForRoles(['Publisher'], ['Viewer', 'Curator']), false);
  });

  it('空白区切りのロール指定を配列にし、未定義のロールを検出する', () => {
    const roles = model.parseTokenList('  Publisher   Curatr ');
    assert.deepEqual(roles, ['Publisher', 'Curatr']);
    assert.deepEqual(model.findUnknownRoles(roles), ['Curatr']);
  });

  it('未定義のプリセットは既定の全ロールに戻す', () => {
    assert.equal(model.resolveRolePreset('curator'), 'curator');
    assert.equal(model.resolveRolePreset('unknown'), model.DEFAULT_ROLE_PRESET);
    assert.equal(model.resolveRolePreset(null), model.DEFAULT_ROLE_PRESET);
    assert.equal(model.resolveRolePreset('toString'), model.DEFAULT_ROLE_PRESET);
  });
});

describe('表示状態の判定', () => {
  it('未定義の表示状態は正常に戻す', () => {
    assert.equal(model.resolveViewState('error403'), 'error403');
    assert.equal(model.resolveViewState('broken'), model.DEFAULT_VIEW_STATE);
  });

  it('一覧領域は既定で準備中と未取得を表現しない', () => {
    assert.equal(model.regionShowsState([], 'error5xx'), true);
    assert.equal(model.regionShowsState([], 'preparing'), false);
    assert.equal(model.regionShowsState([], 'notFetched'), false);
  });

  it('明示した表示状態だけを表現する', () => {
    assert.equal(model.regionShowsState(['preparing'], 'preparing'), true);
    assert.equal(model.regionShowsState(['preparing'], 'empty'), false);
  });
});

describe('廃止の二重確認', () => {
  const expectedName = 'legacy-contract-mcp';

  it('確認チェックとレコード名の一致がそろったときだけ実行できる', () => {
    assert.equal(model.isDeprecationConfirmed({ checked: true, typedName: expectedName, expectedName }), true);
    assert.equal(
      model.isDeprecationConfirmed({ checked: true, typedName: ` ${expectedName} `, expectedName }),
      true,
    );
  });

  it('チェックがない、または名前が一致しなければ実行できない', () => {
    assert.equal(model.isDeprecationConfirmed({ checked: false, typedName: expectedName, expectedName }), false);
    assert.equal(model.isDeprecationConfirmed({ checked: true, typedName: 'legacy', expectedName }), false);
  });

  it('期待する名前が未設定なら実行できない', () => {
    assert.equal(model.isDeprecationConfirmed({ checked: true, typedName: '', expectedName: '' }), false);
  });
});

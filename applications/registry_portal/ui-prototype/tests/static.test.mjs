// HTML と JS の静的検査。
// CSP 準拠（Issue #83 の受け入れ条件）、ローカルリンクの整合性、data-* 属性の値が定義済みであること、用語を確かめる。
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { describe, it } from 'node:test';

import {
  PROTOTYPE_DIR,
  collectAttributeValues,
  listHtmlFiles,
  listScriptFiles,
  loadModel,
  readText,
} from './helpers.mjs';

const model = loadModel();
const htmlFiles = listHtmlFiles();
const scriptFiles = listScriptFiles();
const html = Object.fromEntries(htmlFiles.map((name) => [name, readText(path.join(PROTOTYPE_DIR, name))]));
const scripts = Object.fromEntries(
  scriptFiles.map((name) => [name, readText(path.join(PROTOTYPE_DIR, 'assets', name))]),
);

const EXPECTED_SCRIPTS = [
  './assets/prototype-model.js',
  './assets/prototype.js',
  './assets/prototype-pages.js',
];
const VIEW_STATE_KEYS = model.VIEW_STATES.map((state) => state.key);

/** GLOSSARY.md の _Avoid_ のうち、文脈によらず使わない語と、過去のレビューで指摘された語 */
const AVOIDED_TERMS = [
  '改ざん',
  'リカバリー',
  '監査ログ',
  '操作ログ',
  '承認者',
  '承認リクエスト',
  'レビュー依頼',
  '強制ロック解除',
  '野良レコード',
  '不正変更',
  '外部更新',
  '非公開化',
  '可視性',
  '公開範囲',
];

describe('CSP 準拠（style-src / script-src / font-src が self のみ）', () => {
  it('11 画面の HTML がある', () => {
    assert.equal(htmlFiles.length, 11);
  });

  for (const name of htmlFiles) {
    it(`${name}: インライン style・script・イベント属性・外部読み込みがない`, () => {
      const source = html[name];
      assert.doesNotMatch(source, /<style[\s>]/i, '<style> 要素があります');
      assert.doesNotMatch(source, /\sstyle\s*=/i, 'style 属性があります');
      assert.doesNotMatch(source, /\son[a-z]+\s*=/i, 'on* イベント属性があります');
      assert.doesNotMatch(source, /<script(?![^>]*\ssrc=)[^>]*>/i, 'インライン <script> があります');
      assert.doesNotMatch(source, /<(?:script|link|img|iframe)[^>]*\s(?:src|href)="(?:https?:)?\/\//i, '外部リソースを読み込んでいます');
    });

    it(`${name}: 定義・共通シェル・画面デモの順に読み込む`, () => {
      assert.deepEqual(collectAttributeValues(html[name], 'src'), EXPECTED_SCRIPTS);
    });
  }

  for (const name of scriptFiles) {
    it(`assets/${name}: style の直接操作とイベント属性の生成がない`, () => {
      const source = scripts[name];
      assert.doesNotMatch(source, /\.style\b/, 'style を直接操作しています');
      assert.doesNotMatch(source, /\son[a-z]+="/, 'on* イベント属性を生成しています');
      assert.doesNotMatch(source, /\beval\(|new Function\(/, '文字列からコードを実行しています');
    });
  }
});

describe('ローカルリンク', () => {
  for (const name of htmlFiles) {
    it(`${name}: リンク先のファイルとアンカーが存在する`, () => {
      for (const href of collectAttributeValues(html[name], 'href')) {
        if (!href.startsWith('./') || href.startsWith('./assets/')) continue;
        const [file, anchor] = href.slice(2).split('#');
        const target = file || name;
        assert.ok(existsSync(path.join(PROTOTYPE_DIR, target)), `${href} のファイルがありません`);
        if (anchor) {
          const targetHtml = html[target];
          const found = [' id', ' data-tab-hash', ' data-tab-target'].some((attr) =>
            targetHtml.includes(`${attr}="${anchor}"`),
          );
          assert.ok(found, `${href} のアンカーが ${target} にありません`);
        }
      }
    });
  }

  it('共通シェルのリンク先が存在する', () => {
    const shellHrefs = [
      ...model.NAV_SECTIONS.flatMap((section) => section.items.map((item) => item.href)),
      model.STATES_NAV_ITEM.href,
    ];
    for (const href of shellHrefs) {
      assert.ok(existsSync(path.join(PROTOTYPE_DIR, href)), `${href} のファイルがありません`);
    }
    assert.ok(html['states.html'].includes('id="notifications-states"'));
  });
});

describe('data-* 属性の値が prototype-model.js に定義されている', () => {
  for (const name of htmlFiles) {
    it(name, () => {
      const source = html[name];
      for (const value of collectAttributeValues(source, 'data-requires-roles')) {
        assert.deepEqual(model.findUnknownRoles(model.parseTokenList(value)), [], `業務ロール: ${value}`);
      }
      for (const value of collectAttributeValues(source, 'data-options')) {
        assert.ok(model.OPTION_SETS[value], `選択肢: ${value}`);
      }
      for (const value of collectAttributeValues(source, 'data-history-drawer')) {
        assert.ok(model.HISTORY_SAMPLES[value], `履歴サンプル: ${value}`);
      }
      for (const attr of ['data-state-sample', 'data-state-kinds', 'data-state-only', 'data-state-hide']) {
        for (const value of collectAttributeValues(source, attr)) {
          for (const key of model.parseTokenList(value)) {
            assert.ok(VIEW_STATE_KEYS.includes(key), `${attr}: ${key}`);
          }
        }
      }
    });
  }

  it('選択肢を HTML に直接書かず、単一定義から描画する', () => {
    for (const name of htmlFiles) {
      for (const value of ['AGENT', 'a2aAgentCard', 'agentSkillsDefinition', 'private']) {
        assert.doesNotMatch(html[name], new RegExp(`<option value="${value}"`), `${name} に ${value} の選択肢が直接書かれています`);
      }
    }
  });
});

describe('用語（GLOSSARY.md）', () => {
  const targets = { ...html, ...scripts };
  for (const [name, source] of Object.entries(targets)) {
    it(`${name}: 避ける用語を使っていない`, () => {
      for (const term of AVOIDED_TERMS) {
        assert.ok(!source.includes(term), `「${term}」を使っています`);
      }
    });
  }
});

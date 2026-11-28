// テスト共通: プロトタイプの定義（prototype-model.js）と各ファイルの読み込み。
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

export const PROTOTYPE_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const FRONTEND_SRC_DIR = path.resolve(PROTOTYPE_DIR, '../frontend/src');
export const REPO_ROOT = path.resolve(PROTOTYPE_DIR, '../../..');

export function readText(filePath) {
  return readFileSync(filePath, 'utf-8');
}

/**
 * ブラウザと同じく、prototype-model.js をグローバルに RegistryPrototypeModel を置くスクリプトとして実行する。
 * 配列の比較（assert.deepStrictEqual）が realm の違いで失敗しないよう、テストと同じ realm で実行する
 */
export function loadModel() {
  vm.runInThisContext(readText(path.join(PROTOTYPE_DIR, 'assets/prototype-model.js')), {
    filename: 'prototype-model.js',
  });
  return globalThis.RegistryPrototypeModel;
}

export function listHtmlFiles() {
  return readdirSync(PROTOTYPE_DIR)
    .filter((name) => name.endsWith('.html'))
    .sort();
}

export function listScriptFiles() {
  return readdirSync(path.join(PROTOTYPE_DIR, 'assets'))
    .filter((name) => name.endsWith('.js'))
    .sort();
}

/** HTML 中の属性値を、属性名ごとにすべて集める */
export function collectAttributeValues(html, attribute) {
  const pattern = new RegExp(`\\s${attribute}="([^"]*)"`, 'g');
  return [...html.matchAll(pattern)].map((match) => match[1]);
}

/** frontend の TypeScript から `export const NAME = [ ... ] as const` の文字列要素を取り出す */
export function extractStringArray(source, name) {
  const match = source.match(new RegExp(`export const ${name}\\b[^=]*=\\s*\\[([\\s\\S]*?)\\]`));
  if (!match) throw new Error(`${name} の定義が見つかりません`);
  return [...match[1].matchAll(/'([^']*)'/g)].map((m) => m[1]);
}

/** frontend の TypeScript から `export const NAME: Record<...> = { KEY: '値', ... }` を取り出す */
export function extractStringRecord(source, name) {
  const match = source.match(new RegExp(`export const ${name}\\b[^=]*=\\s*\\{([\\s\\S]*?)\\n\\}`));
  if (!match) throw new Error(`${name} の定義が見つかりません`);
  return Object.fromEntries(
    [...match[1].matchAll(/(\w+):\s*'([^']*)'/g)].map((m) => [m[1], m[2]]),
  );
}

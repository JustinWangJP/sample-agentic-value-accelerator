import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const context = vm.createContext({});
vm.runInContext(readFileSync(new URL('../spa_rewrite.js', import.meta.url), 'utf8'), context);
const request = (uri, method = 'GET') => context.handler({ request: { uri, method } });

test('深い SPA URL を index.html に解決する', () => {
  assert.equal(request('/catalog/agent/123').uri, '/index.html');
  assert.equal(request('/').uri, '/index.html');
});

test('静的ファイルは失敗時も SPA に差し替えない', () => {
  assert.equal(request('/assets/missing.js').uri, '/assets/missing.js');
  assert.equal(request('/missing.css').uri, '/missing.css');
});

test('API origin の取り違えは JSON の 404 で拒否する', () => {
  for (const uri of ['/api', '/api/me', '/api/missing']) {
    const response = request(uri);
    assert.equal(response.statusCode, 404);
    assert.equal(response.headers['content-type'].value, 'application/json');
    assert.doesNotMatch(response.body, /<html/i);
  }
});

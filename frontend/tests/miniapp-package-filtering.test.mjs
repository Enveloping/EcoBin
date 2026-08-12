import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

function json(relativePath) {
  return JSON.parse(readFileSync(new URL(relativePath, import.meta.url), 'utf8'));
}

test('miniapp preview and upload both filter files outside the dependency graph', () => {
  const project = json('../miniprogram/project.config.json');

  assert.equal(project.setting.ignoreDevUnusedFiles, true);
  assert.equal(project.setting.ignoreUploadUnusedFiles, true);
});

test('global TDesign registration keeps only the component used by live pages', () => {
  const app = json('../miniprogram/miniprogram/app.json');

  assert.deepEqual(app.usingComponents, {
    't-icon': 'tdesign-miniprogram/icon/icon',
  });
});

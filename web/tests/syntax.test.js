/**
 * Syntax validation tests for all web/js/*.js files.
 * Runs `node --check` on each file to verify valid ES module syntax.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readdirSync } from 'node:fs';
import path from 'node:path';

const JS_DIR = path.resolve(process.cwd(), 'web/js');

describe('JavaScript syntax validation', () => {
  const files = readdirSync(JS_DIR).filter(f => f.endsWith('.js'));

  for (const file of files) {
    const filePath = path.join(JS_DIR, file);
    it(`${file} parses as valid ES module`, () => {
      const result = spawnSync(process.execPath, ['--check', filePath], {
        encoding: 'utf8',
      });
      assert.equal(result.status, 0,
        `${file} failed syntax check:\n${result.stderr}`);
    });
  }
});
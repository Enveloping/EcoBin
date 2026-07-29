import { gzipSync } from 'node:zlib';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const dist = resolve('dist');
const html = readFileSync(resolve(dist, 'index.html'), 'utf8');
const entryMatch = html.match(
  /<script[^>]+type="module"[^>]+src="([^"]+\.js)"/,
);

if (!entryMatch) {
  throw new Error('Unable to locate the direct JavaScript entry in dist/index.html');
}

const entryPath = resolve(dist, entryMatch[1].replace(/^\//, ''));
const gzipBytes = gzipSync(readFileSync(entryPath)).byteLength;
const budgetBytes = 500 * 1024;

if (gzipBytes > budgetBytes) {
  throw new Error(
    `Direct entry is ${(gzipBytes / 1024).toFixed(1)} KiB gzip; budget is 500 KiB`,
  );
}

console.log(
  `PASS: direct entry ${(gzipBytes / 1024).toFixed(1)} KiB gzip (budget 500 KiB)`,
);

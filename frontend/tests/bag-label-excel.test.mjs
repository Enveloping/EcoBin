import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';

import {
  buildBagCodeExcel,
  buildBagQrExcel,
} from '../web/src/pages/bag-labels/excelExport.ts';
import {
  MAX_BAG_LABEL_BATCH_QUANTITY,
} from '../web/src/pages/bag-labels/bagLabelLimits.ts';

const require = createRequire(new URL('../web/package.json', import.meta.url));
const ExcelJS = require('exceljs');

const labels = [
  {
    sequenceNo: 3,
    bagCode: 'EB1_K1_000G40R40M30E209185GR38E1Y_GRQ320Z8YDWC8V49M7W2',
    qrPayload: 'EB1_K1_000G40R40M30E209185GR38E1Y_GRQ320Z8YDWC8V49M7W2',
  },
  {
    sequenceNo: 1,
    bagCode: 'EB1_K1_000G40R40M30E209185GR38E1W_GRQ320Z8YDWC8V49M7W0',
    qrPayload: 'EB1_K1_000G40R40M30E209185GR38E1W_GRQ320Z8YDWC8V49M7W0',
  },
  {
    sequenceNo: 2,
    bagCode: 'EB1_K1_000G40R40M30E209185GR38E1X_GRQ320Z8YDWC8V49M7W1',
    qrPayload: 'EB1_K1_000G40R40M30E209185GR38E1X_GRQ320Z8YDWC8V49M7W1',
  },
];

const batch = {
  batchUid: '10000000-0000-4000-8000-000000000001',
  keyId: 'K1',
  quantity: labels.length,
  createdBy: {
    platformAdminUid: '20000000-0000-4000-8000-000000000001',
    displayName: '测试管理员',
  },
  createdAt: '2026-08-25T00:00:00Z',
  labels,
};

async function readWorkbook(buffer) {
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load(buffer);
  return workbook;
}

test('bag-code Excel is exactly one headerless text column in sequence order', async () => {
  const workbook = await readWorkbook(await buildBagCodeExcel(batch));

  assert.equal(workbook.worksheets.length, 1);
  const sheet = workbook.worksheets[0];
  assert.equal(sheet.name, '袋码');
  assert.equal(sheet.actualRowCount, labels.length);
  assert.equal(sheet.actualColumnCount, 1);
  assert.deepEqual(
    [1, 2, 3].map((row) => sheet.getCell(row, 1).value),
    labels
      .toSorted((left, right) => left.sequenceNo - right.sequenceNo)
      .map((label) => label.bagCode),
  );
  assert.equal(sheet.getCell('B1').value, null);
  assert.equal(sheet.getImages().length, 0);
});

test('QR Excel contains images only and no visible cell text', async () => {
  const workbook = await readWorkbook(await buildBagQrExcel(batch));

  assert.equal(workbook.worksheets.length, 1);
  const sheet = workbook.worksheets[0];
  assert.equal(sheet.name, '二维码');
  assert.equal(sheet.getImages().length, labels.length);
  sheet.eachRow({ includeEmpty: true }, (row) => {
    row.eachCell({ includeEmpty: true }, (cell) => {
      assert.equal(cell.value, null);
    });
  });
});

test('all clients use the frozen 500-label batch ceiling', () => {
  assert.equal(MAX_BAG_LABEL_BATCH_QUANTITY, 500);
});

test('the low-frequency Excel exporter stays behind a click-time import', () => {
  const page = readFileSync(
    new URL('../web/src/pages/bag-labels/index.tsx', import.meta.url),
    'utf8',
  );

  assert.match(page, /await import\('\.\/excelExport\.ts'\)/);
  assert.doesNotMatch(page, /^import .*excelExport/m);
});

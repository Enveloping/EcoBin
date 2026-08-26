import QrCodeEncoder from 'qrcode';
import type { Workbook } from 'exceljs';
import type { BagLabelBatch, BagLabelItem } from '../../api/bagLabels';
import { MAX_BAG_LABEL_BATCH_QUANTITY } from './bagLabelLimits.ts';

const XLSX_MIME =
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
const QR_COLUMNS = 4;
const QR_IMAGE_SIZE = 146;
const QR_ROW_HEIGHT = 114;

async function createWorkbook(): Promise<Workbook> {
  const { default: excel } = await import('exceljs');
  return new excel.Workbook();
}

function orderedLabels(batch: BagLabelBatch): BagLabelItem[] {
  if (!Number.isInteger(batch.quantity)
      || batch.quantity < 1
      || batch.quantity > MAX_BAG_LABEL_BATCH_QUANTITY
      || batch.labels.length !== batch.quantity) {
    throw new Error('袋码批次数量不完整，无法导出');
  }
  const labels = [...batch.labels]
    .sort((left, right) => left.sequenceNo - right.sequenceNo);
  labels.forEach((label, index) => {
    if (label.sequenceNo !== index + 1 || !label.bagCode) {
      throw new Error('袋码批次顺序不连续，无法导出');
    }
  });
  return labels;
}

function workbookName(prefix: string, batch: BagLabelBatch): string {
  return `${prefix}_${batch.batchUid.slice(0, 8)}_${batch.quantity}个.xlsx`;
}

function download(buffer: ArrayBuffer, name: string) {
  const url = URL.createObjectURL(new Blob([buffer], { type: XLSX_MIME }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

export async function buildBagCodeExcel(
  batch: BagLabelBatch,
): Promise<ArrayBuffer> {
  const labels = orderedLabels(batch);
  const workbook = await createWorkbook();
  workbook.creator = 'EcoBin';
  workbook.title = 'EcoBin 袋码内容';
  workbook.subject = '单列、无表头的袋码内容';
  const sheet = workbook.addWorksheet('袋码', {
    pageSetup: {
      orientation: 'portrait',
      printArea: `A1:A${labels.length}`,
    },
    views: [{ zoomScale: 90 }],
  });
  sheet.getColumn(1).width = 58;
  labels.forEach((label, index) => {
    const row = sheet.getRow(index + 1);
    row.height = 18;
    const cell = row.getCell(1);
    cell.value = label.bagCode;
    cell.numFmt = '@';
    cell.font = { name: 'Arial', size: 10, color: { argb: 'FF000000' } };
    cell.alignment = { horizontal: 'left', vertical: 'middle' };
  });
  return workbook.xlsx.writeBuffer();
}

export async function buildBagQrExcel(
  batch: BagLabelBatch,
): Promise<ArrayBuffer> {
  const labels = orderedLabels(batch);
  const workbook = await createWorkbook();
  workbook.creator = 'EcoBin';
  workbook.title = 'EcoBin 袋码二维码';
  workbook.subject = '仅包含二维码图片的袋码工作表';
  const rowCount = Math.ceil(labels.length / QR_COLUMNS);
  const sheet = workbook.addWorksheet('二维码', {
    pageSetup: {
      paperSize: 9,
      orientation: 'portrait',
      fitToPage: true,
      fitToWidth: 1,
      fitToHeight: 0,
      horizontalCentered: true,
      printArea: `A1:D${rowCount}`,
      margins: {
        left: 0.2,
        right: 0.2,
        top: 0.25,
        bottom: 0.25,
        header: 0,
        footer: 0,
      },
    },
    views: [{ showGridLines: false, zoomScale: 75 }],
  });
  for (let column = 1; column <= QR_COLUMNS; column += 1) {
    sheet.getColumn(column).width = 22;
  }
  for (let row = 1; row <= rowCount; row += 1) {
    sheet.getRow(row).height = QR_ROW_HEIGHT;
  }
  for (let row = 6; row < rowCount; row += 6) {
    sheet.getRow(row).addPageBreak();
  }

  for (let index = 0; index < labels.length; index += 1) {
    const label = labels[index];
    const base64 = await QrCodeEncoder.toDataURL(label.qrPayload, {
      type: 'image/png',
      width: 420,
      margin: 4,
      errorCorrectionLevel: 'M',
      color: { dark: '#000000', light: '#ffffff' },
    });
    const imageId = workbook.addImage({ base64, extension: 'png' });
    sheet.addImage(imageId, {
      tl: {
        col: index % QR_COLUMNS,
        row: Math.floor(index / QR_COLUMNS),
      },
      ext: { width: QR_IMAGE_SIZE, height: QR_IMAGE_SIZE },
      editAs: 'oneCell',
    });
    if ((index + 1) % 16 === 0) {
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    }
  }
  return workbook.xlsx.writeBuffer();
}

export async function downloadBagCodeExcel(batch: BagLabelBatch) {
  download(
    await buildBagCodeExcel(batch),
    workbookName('袋码内容', batch),
  );
}

export async function downloadBagQrExcel(batch: BagLabelBatch) {
  download(
    await buildBagQrExcel(batch),
    workbookName('袋码二维码', batch),
  );
}

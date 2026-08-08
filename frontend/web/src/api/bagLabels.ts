import type { CommandIntent } from './commandIntent';
import request from './request';
import type { components, operations } from './generated/openapi';

type Schemas = components['schemas'];

export type PlatformAdminSummary = Schemas['BagLabelPlatformAdmin'];
export type BagLabelBatchSummary = Schemas['BagLabelBatchSummary'];
export type BagLabelItem = Schemas['BagLabelItem'];
export type BagLabelBatch = Schemas['BagLabelBatch'];
export type BagLabelBatchPage = Schemas['BagLabelBatchPage'];
export type CreateBagLabelBatchRequest =
  Schemas['CreateBagLabelBatchRequest'];
export type BagLabelBatchListParams = NonNullable<
  operations['listPlatformBagLabelBatches']['parameters']['query']
>;

const COLLECTION = '/api/v1/web/platform/bag-label-batches';

export function listBagLabelBatches(params: BagLabelBatchListParams = {}) {
  return request<BagLabelBatchPage>({
    url: COLLECTION,
    method: 'GET',
    params,
    noStore: true,
    silent: true,
  });
}

export function getBagLabelBatch(batchUid: string) {
  return request<BagLabelBatch>({
    url: `${COLLECTION}/${encodeURIComponent(batchUid)}`,
    method: 'GET',
    noStore: true,
    silent: true,
  });
}

export function createBagLabelBatch(
  data: CreateBagLabelBatchRequest,
  intent: CommandIntent,
) {
  return intent.execute<BagLabelBatch, CreateBagLabelBatchRequest>({
    url: COLLECTION,
    method: 'POST',
    data,
    silent: true,
  });
}

export function deleteBagLabelBatch(batchUid: string) {
  return request<void>({
    url: `${COLLECTION}/${encodeURIComponent(batchUid)}`,
    method: 'DELETE',
    silent: true,
  });
}

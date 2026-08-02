import type { CommandIntent } from './commandIntent';
import type { components, operations } from './generated/openapi';
import type { DirectoryContext } from './identityDirectory';
import request from './request';

type Schemas = components['schemas'];

export type DeliveryOrderItem = Schemas['WebDeliveryOrderItem'];
export type DeliveryOrderDetail = Schemas['WebDeliveryOrderDetail'];
export type DeliveryOrderCursorPage = Schemas['DeliveryOrderCursorPage'];
export type DeliveryReviewStatus = Schemas['DeliveryReviewStatus'];
export type DeliveryReviewDecision = Schemas['DeliveryReviewDecision'];
export type DeliveryPhotoCompleteness =
  Schemas['DeliveryPhotoCompleteness'];
export type DeliveryReviewRequest = Schemas['ReviewDeliveryOrderRequest'];
export type DeliveryReviewResult = Schemas['DeliveryReviewResult'];
export type DeliveryReviewPreviewRequest =
  Schemas['PreviewDeliveryReviewRequest'];
export type DeliveryReviewPreview = Schemas['DeliveryReviewPreview'];
export type DeliveryOrderListParams = NonNullable<
  operations['listWebDeliveryOrders']['parameters']['query']
>;

function collectionUrl(
  context: DirectoryContext,
  organizationCode: string,
): string {
  const organization = encodeURIComponent(organizationCode);
  if (context.domain === 'platform') {
    if (!context.tenantCode) {
      throw new Error('平台投递订单查询需要先选择目标租户');
    }
    return (
      `/api/v1/web/platform/tenants/${encodeURIComponent(context.tenantCode)}`
      + `/organizations/${organization}/delivery-orders`
    );
  }
  return `/api/v1/web/organizations/${organization}/delivery-orders`;
}

export function listDeliveryOrders(
  context: DirectoryContext,
  organizationCode: string,
  params: DeliveryOrderListParams = {},
) {
  return request<DeliveryOrderCursorPage>({
    url: collectionUrl(context, organizationCode),
    method: 'GET',
    params,
    noStore: true,
  });
}

export function getDeliveryOrder(
  context: DirectoryContext,
  organizationCode: string,
  deliveryOrderNo: string,
) {
  return request<DeliveryOrderDetail>({
    url:
      `${collectionUrl(context, organizationCode)}/`
      + encodeURIComponent(deliveryOrderNo),
    method: 'GET',
    noStore: true,
  });
}

export function previewDeliveryOrderReview(
  context: DirectoryContext,
  organizationCode: string,
  deliveryOrderNo: string,
  data: DeliveryReviewPreviewRequest,
  signal?: AbortSignal,
) {
  return request<DeliveryReviewPreview, DeliveryReviewPreviewRequest>({
    url:
      `${collectionUrl(context, organizationCode)}/`
      + `${encodeURIComponent(deliveryOrderNo)}/review-previews`,
    method: 'POST',
    data,
    signal,
    noStore: true,
    silent: true,
  });
}

function submitDeliveryReview(
  context: DirectoryContext,
  organizationCode: string,
  deliveryOrderNo: string,
  operation: 'reviews' | 'corrections',
  data: DeliveryReviewRequest,
  intent: CommandIntent,
) {
  return intent.execute<DeliveryReviewResult, DeliveryReviewRequest>({
    url:
      `${collectionUrl(context, organizationCode)}/`
      + `${encodeURIComponent(deliveryOrderNo)}/${operation}`,
    method: 'POST',
    data,
  });
}

export function reviewDeliveryOrder(
  context: DirectoryContext,
  organizationCode: string,
  deliveryOrderNo: string,
  data: DeliveryReviewRequest,
  intent: CommandIntent,
) {
  return submitDeliveryReview(
    context,
    organizationCode,
    deliveryOrderNo,
    'reviews',
    data,
    intent,
  );
}

export function correctDeliveryOrder(
  context: DirectoryContext,
  organizationCode: string,
  deliveryOrderNo: string,
  data: DeliveryReviewRequest,
  intent: CommandIntent,
) {
  return submitDeliveryReview(
    context,
    organizationCode,
    deliveryOrderNo,
    'corrections',
    data,
    intent,
  );
}

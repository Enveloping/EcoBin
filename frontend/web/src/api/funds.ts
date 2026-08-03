import type { CommandIntent } from './commandIntent';
import type { components } from './generated/openapi';
import type { DirectoryContext } from './identityDirectory';
import request from './request';

type Schemas = components['schemas'];

export type PayoutAccount = Schemas['PayoutAccountView'];
export type PayoutGate = Schemas['PayoutGateView'];
export type RestorePayoutGateRequest = Schemas['RestorePayoutGateRequest'];
export type RechargeOrder = Schemas['RechargeView'];
export type RechargePage = Schemas['RechargePage'];
export type CreateRechargeRequest = Schemas['CreateRechargeRequest'];
export type WithdrawalOrder = Schemas['WithdrawalView'];
export type WithdrawalPage = Schemas['WithdrawalPage'];
export type ReviewWithdrawalRequest = Schemas['ReviewWithdrawalRequest'];
export type WithdrawalConfiguration =
  Schemas['WithdrawalConfigurationView'];
export type MerchantBinding = Schemas['MerchantBindingView'];
export type VerifyMerchantBindingRequest =
  Schemas['VerifyMerchantBindingRequest'];
export type DisableMerchantBindingRequest =
  Schemas['DisableMerchantBindingRequest'];
export type WalletSummary = Schemas['WalletSummary'];
export type AdjustWalletRequest = Schemas['AdjustWalletRequest'];
export type WalletAdjustment = Schemas['WalletAdjustmentView'];

function organizationBase(
  context: DirectoryContext,
  organizationCode: string,
): string {
  const organization = encodeURIComponent(organizationCode);
  if (context.domain === 'platform') {
    if (!context.tenantCode) {
      throw new Error('平台资金查询需要先选择目标租户');
    }
    return (
      `/api/v1/web/platform/tenants/${encodeURIComponent(context.tenantCode)}`
      + `/organizations/${organization}`
    );
  }
  return `/api/v1/web/organizations/${organization}`;
}

export function getPayoutAccount(
  context: DirectoryContext,
  organizationCode: string,
) {
  return request<PayoutAccount>({
    url: `${organizationBase(context, organizationCode)}/payout-account`,
    method: 'GET',
    noStore: true,
  });
}

export function getOrganizationUserWallet(
  context: DirectoryContext,
  organizationCode: string,
  organizationUserUid: string,
) {
  return request<WalletSummary>({
    url:
      `${organizationBase(context, organizationCode)}/organization-users/`
      + `${encodeURIComponent(organizationUserUid)}/wallet`,
    method: 'GET',
    noStore: true,
  });
}

export function adjustOrganizationUserWallet(
  context: DirectoryContext,
  organizationCode: string,
  organizationUserUid: string,
  data: AdjustWalletRequest,
  intent: CommandIntent,
) {
  return intent.execute<WalletAdjustment, AdjustWalletRequest>({
    url:
      `${organizationBase(context, organizationCode)}/organization-users/`
      + `${encodeURIComponent(organizationUserUid)}/wallet-adjustments`,
    method: 'POST',
    data,
  });
}

export function getPayoutGate() {
  return request<PayoutGate>({
    url: '/api/v1/web/platform/payout-gate',
    method: 'GET',
    noStore: true,
  });
}

export function restorePayoutGate(
  data: RestorePayoutGateRequest,
  intent: CommandIntent,
) {
  return intent.execute<PayoutGate, RestorePayoutGateRequest>({
    url: '/api/v1/web/platform/payout-gate/restorations',
    method: 'POST',
    data,
  });
}

export function listRechargeOrders(
  context: DirectoryContext,
  organizationCode: string,
  params: { status?: string; limit?: number } = {},
) {
  return request<RechargePage>({
    url: `${organizationBase(context, organizationCode)}/recharge-orders`,
    method: 'GET',
    params,
    noStore: true,
  });
}

export function createRechargeOrder(
  context: DirectoryContext,
  organizationCode: string,
  data: CreateRechargeRequest,
  intent: CommandIntent,
) {
  if (context.domain === 'platform') {
    throw new Error('充值必须由目标租户工作人员发起');
  }
  return intent.execute<RechargeOrder, CreateRechargeRequest>({
    url: `${organizationBase(context, organizationCode)}/recharge-orders`,
    method: 'POST',
    data,
  });
}

export function listWithdrawals(
  context: DirectoryContext,
  organizationCode: string,
  params: { status?: string; limit?: number } = {},
) {
  return request<WithdrawalPage>({
    url: `${organizationBase(context, organizationCode)}/withdrawals`,
    method: 'GET',
    params,
    noStore: true,
  });
}

export function reviewWithdrawal(
  context: DirectoryContext,
  organizationCode: string,
  withdrawalNo: string,
  data: ReviewWithdrawalRequest,
  intent: CommandIntent,
) {
  return intent.execute<WithdrawalOrder, ReviewWithdrawalRequest>({
    url:
      `${organizationBase(context, organizationCode)}/withdrawals/`
      + `${encodeURIComponent(withdrawalNo)}/reviews`,
    method: 'POST',
    data,
  });
}

export function getWithdrawalConfiguration(
  context: DirectoryContext,
  organizationCode: string,
) {
  return request<WithdrawalConfiguration>({
    url:
      `${organizationBase(context, organizationCode)}`
      + '/withdrawal-configuration',
    method: 'GET',
    noStore: true,
  });
}

export function getMerchantBinding(
  context: DirectoryContext,
  organizationCode: string,
) {
  if (context.domain !== 'platform') {
    throw new Error('商户绑定核查只允许平台管理员访问');
  }
  return request<MerchantBinding>({
    url:
      `${organizationBase(context, organizationCode)}`
      + '/wechat-merchant-binding',
    method: 'GET',
    noStore: true,
  });
}

export function verifyMerchantBinding(
  context: DirectoryContext,
  organizationCode: string,
  data: VerifyMerchantBindingRequest,
  intent: CommandIntent,
) {
  return intent.execute<MerchantBinding, VerifyMerchantBindingRequest>({
    url:
      `${organizationBase(context, organizationCode)}`
      + '/wechat-merchant-binding/verifications',
    method: 'POST',
    data,
  });
}

export function disableMerchantBinding(
  context: DirectoryContext,
  organizationCode: string,
  data: DisableMerchantBindingRequest,
  intent: CommandIntent,
) {
  return intent.execute<MerchantBinding, DisableMerchantBindingRequest>({
    url:
      `${organizationBase(context, organizationCode)}`
      + '/wechat-merchant-binding/disablements',
    method: 'POST',
    data,
  });
}

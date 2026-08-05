import { Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';
import type {
  WalletEntrySourceType,
  WalletEntryType,
} from '@/api/wallet';
import { directoryPath } from '@/router/directoryQuery';
import {
  compareMoneyCny,
  formatMoneyCny,
} from '@/utils/decimal';

export const walletEntryTypeLabels: Record<WalletEntryType, string> = {
  DELIVERY_INITIAL_REVIEW: '投递首次审核',
  DELIVERY_CORRECTION: '投递纠正',
  WITHDRAWAL_FREEZE: '提现冻结',
  WITHDRAWAL_SUCCEEDED: '提现成功',
  WITHDRAWAL_RELEASED: '提现释放',
  MANUAL_ADJUSTMENT: '人工调整',
};

const walletEntryTypeColors: Record<WalletEntryType, string> = {
  DELIVERY_INITIAL_REVIEW: 'green',
  DELIVERY_CORRECTION: 'orange',
  WITHDRAWAL_FREEZE: 'blue',
  WITHDRAWAL_SUCCEEDED: 'cyan',
  WITHDRAWAL_RELEASED: 'geekblue',
  MANUAL_ADJUSTMENT: 'default',
};

const sourceTypeLabels: Record<WalletEntrySourceType, string> = {
  DELIVERY_ORDER: '投递订单',
  WITHDRAWAL_ORDER: '提现订单',
  MANUAL_ADJUSTMENT: '人工调整',
};

export function WalletEntryTypeTag({
  type,
}: {
  type: WalletEntryType;
}) {
  return (
    <Tag color={walletEntryTypeColors[type]}>
      {walletEntryTypeLabels[type]}
    </Tag>
  );
}

export function WalletMoneyDelta({
  value,
}: {
  value: string;
}) {
  const comparison = compareMoneyCny(value, '0.00');
  return (
    <Typography.Text
      className={[
        'wallet-money-delta',
        comparison > 0
          ? 'wallet-money-positive'
          : comparison < 0
            ? 'wallet-money-negative'
            : 'wallet-money-neutral',
      ].join(' ')}
    >
      {comparison > 0 ? '+' : ''}
      ¥ {formatMoneyCny(value)}
    </Typography.Text>
  );
}

interface WalletSourceProps {
  sourceType: WalletEntrySourceType;
  sourceNo: string;
  tenantCode?: string;
  organizationCode: string;
  canReadDelivery: boolean;
}

export function WalletSource({
  sourceType,
  sourceNo,
  tenantCode,
  organizationCode,
  canReadDelivery,
}: WalletSourceProps) {
  const linksToDelivery =
    sourceType === 'DELIVERY_ORDER' && canReadDelivery;
  const source = (
    <Typography.Text copyable={!linksToDelivery}>
      {sourceNo}
    </Typography.Text>
  );
  return (
    <div className="wallet-source">
      <Tag>{sourceTypeLabels[sourceType]}</Tag>
      {linksToDelivery ? (
        <Link
          className="table-link"
          to={directoryPath('/deliveries', {
            tenant: tenantCode,
            organization: organizationCode,
            deliveryOrderNo: sourceNo,
          })}
        >
          {sourceNo}
        </Link>
      ) : source}
    </div>
  );
}

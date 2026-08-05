import { useEffect, useRef, useState } from 'react';
import { ArrowRightOutlined, WalletOutlined } from '@ant-design/icons';
import {
  Alert,
  Button,
  Drawer,
  Empty,
  Skeleton,
  Space,
  Table,
  Typography,
  type TableColumnsType,
} from 'antd';
import { Link } from 'react-router-dom';
import type { DirectoryContext } from '@/api/identityDirectory';
import { ApiProblem } from '@/api/request';
import {
  getOrganizationUserWalletSummary,
  listOrganizationUserWalletEntries,
  type PersonalWalletEntry,
  type WalletSummary,
} from '@/api/wallet';
import { directoryPath } from '@/router/directoryQuery';
import {
  compareMoneyCny,
  formatMoneyCny,
  formatShanghaiTime,
} from '@/utils/decimal';
import {
  WalletEntryTypeTag,
  WalletMoneyDelta,
  WalletSource,
} from './WalletEntryPresentation';

interface WalletUser {
  organizationUserUid: string;
  nickname: string;
}

interface OrganizationUserWalletDrawerProps {
  open: boolean;
  context: DirectoryContext | null;
  organizationCode?: string;
  user: WalletUser | null;
  canReadDelivery: boolean;
  onClose: () => void;
}

function requestErrorMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '用户钱包加载失败';
}

function WalletMetric({
  label,
  value,
  tone = 'default',
}: {
  label: string;
  value: string;
  tone?: 'default' | 'pending' | 'negative';
}) {
  return (
    <div className={`wallet-metric wallet-metric-${tone}`}>
      <Typography.Text type="secondary">{label}</Typography.Text>
      <div className="wallet-metric-value">
        ¥ {formatMoneyCny(value)}
      </div>
    </div>
  );
}

export default function OrganizationUserWalletDrawer({
  open,
  context,
  organizationCode,
  user,
  canReadDelivery,
  onClose,
}: OrganizationUserWalletDrawerProps) {
  const requestSequence = useRef(0);
  const [summary, setSummary] = useState<WalletSummary | null>(null);
  const [entries, setEntries] = useState<PersonalWalletEntry[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [entriesAsOf, setEntriesAsOf] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    const requestId = ++requestSequence.current;
    setSummary(null);
    setEntries([]);
    setNextCursor(null);
    setEntriesAsOf(undefined);
    setError(undefined);
    if (!open || !context || !organizationCode || !user) {
      setLoading(false);
      return;
    }
    setLoading(true);
    Promise.all([
      getOrganizationUserWalletSummary(
        context,
        organizationCode,
        user.organizationUserUid,
      ),
      listOrganizationUserWalletEntries(
        context,
        organizationCode,
        user.organizationUserUid,
        { limit: 20 },
      ),
    ])
      .then(([loadedSummary, page]) => {
        if (requestSequence.current !== requestId) return;
        setSummary(loadedSummary);
        setEntries(page.items);
        setNextCursor(page.nextCursor);
        setEntriesAsOf(page.asOf);
      })
      .catch((reason) => {
        if (requestSequence.current === requestId) {
          setError(requestErrorMessage(reason));
        }
      })
      .finally(() => {
        if (requestSequence.current === requestId) setLoading(false);
      });
    return () => {
      requestSequence.current += 1;
    };
  }, [context, open, organizationCode, user]);

  const loadMore = async () => {
    if (
      !context
      || !organizationCode
      || !user
      || !nextCursor
      || loadingMore
    ) {
      return;
    }
    const requestId = requestSequence.current;
    setLoadingMore(true);
    try {
      const page = await listOrganizationUserWalletEntries(
        context,
        organizationCode,
        user.organizationUserUid,
        {
          cursor: nextCursor,
          limit: 20,
        },
      );
      if (requestSequence.current !== requestId) return;
      setEntries((current) => [...current, ...page.items]);
      setNextCursor(page.nextCursor);
      setEntriesAsOf(page.asOf);
    } catch (reason) {
      if (requestSequence.current === requestId) {
        setError(requestErrorMessage(reason));
      }
    } finally {
      if (requestSequence.current === requestId) setLoadingMore(false);
    }
  };

  const columns: TableColumnsType<PersonalWalletEntry> = [
    {
      title: '类型',
      dataIndex: 'entryType',
      width: 145,
      render: (_, entry) => (
        <WalletEntryTypeTag type={entry.entryType} />
      ),
    },
    {
      title: '可提现变动',
      dataIndex: 'availableDeltaYuan',
      width: 125,
      align: 'right',
      render: (_, entry) => (
        <WalletMoneyDelta value={entry.availableDeltaYuan} />
      ),
    },
    {
      title: '处理中变动',
      dataIndex: 'processingDeltaYuan',
      width: 125,
      align: 'right',
      render: (_, entry) => (
        <WalletMoneyDelta value={entry.processingDeltaYuan} />
      ),
    },
    {
      title: '来源',
      dataIndex: 'sourceNo',
      width: 245,
      render: (_, entry) => (
        <WalletSource
          sourceType={entry.sourceType}
          sourceNo={entry.sourceNo}
          tenantCode={
            context?.domain === 'platform' ? context.tenantCode : undefined
          }
          organizationCode={organizationCode ?? ''}
          canReadDelivery={canReadDelivery}
        />
      ),
    },
    {
      title: '发生时间',
      dataIndex: 'occurredAt',
      width: 180,
      render: (_, entry) => formatShanghaiTime(entry.occurredAt),
    },
  ];

  const availableNegative =
    !!summary
    && compareMoneyCny(summary.availableBalanceYuan, '0.00') < 0;
  const historyPath =
    user && organizationCode
      ? directoryPath('/wallet-entries', {
          tenant: context?.domain === 'platform'
            ? context.tenantCode
            : undefined,
          organization: organizationCode,
          organizationUserUid: user.organizationUserUid,
        })
      : '/wallet-entries';

  return (
    <Drawer
      title={(
        <Space wrap>
          <WalletOutlined />
          <span>用户钱包</span>
          {user && (
            <Typography.Text type="secondary">
              {user.nickname}
            </Typography.Text>
          )}
        </Space>
      )}
      width="min(920px, calc(100vw - 24px))"
      open={open}
      onClose={onClose}
      destroyOnClose
      extra={user && organizationCode ? (
        <Link className="table-link" to={historyPath}>
          <Space size={6}>
            <ArrowRightOutlined />
            完整机构流水
          </Space>
        </Link>
      ) : null}
    >
      {loading ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : error && !summary ? (
        <Alert
          showIcon
          type="error"
          message="用户钱包加载失败"
          description={error}
        />
      ) : summary ? (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {availableNegative && (
            <Alert
              showIcon
              type="warning"
              message="当前可提现余额为负"
              description="负余额会阻止用户继续提现；这里仅展示服务端账本投影，不提供人工修改。"
            />
          )}
          {error && (
            <Alert
              showIcon
              type="error"
              message="后续钱包流水加载失败"
              description={error}
            />
          )}
          <div className="wallet-metric-grid">
            <WalletMetric
              label="待审核返现"
              value={summary.pendingRewardYuan}
              tone="pending"
            />
            <WalletMetric
              label="可提现余额"
              value={summary.availableBalanceYuan}
              tone={availableNegative ? 'negative' : 'default'}
            />
            <WalletMetric
              label="提现处理中"
              value={summary.withdrawalProcessingYuan}
            />
          </div>
          <Space wrap>
            <Typography.Text type="secondary">
              摘要快照 {formatShanghaiTime(summary.asOf)}
            </Typography.Text>
            <Typography.Text type="secondary">
              钱包版本 v{summary.walletVersion}
            </Typography.Text>
            {entriesAsOf && (
              <Typography.Text type="secondary">
                流水快照 {formatShanghaiTime(entriesAsOf)}
              </Typography.Text>
            )}
          </Space>
          {entries.length ? (
            <>
              <Table<PersonalWalletEntry>
                rowKey="entryUid"
                size="middle"
                columns={columns}
                dataSource={entries}
                pagination={false}
                scroll={{ x: 820 }}
              />
              {nextCursor && (
                <Button
                  block
                  loading={loadingMore}
                  onClick={loadMore}
                >
                  加载更多流水
                </Button>
              )}
            </>
          ) : (
            <Empty description="该钱包尚无实际资金流水" />
          )}
        </Space>
      ) : (
        <Empty description="未选择机构用户" />
      )}
    </Drawer>
  );
}

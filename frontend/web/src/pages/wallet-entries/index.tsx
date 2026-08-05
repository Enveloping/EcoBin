import { useCallback, useEffect, useRef, useState } from 'react';
import { WalletOutlined } from '@ant-design/icons';
import {
  PageContainer,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import {
  Alert,
  Empty,
  Select,
  Space,
  Spin,
  Typography,
} from 'antd';
import dayjs from 'dayjs';
import { useSearchParams } from 'react-router-dom';
import {
  listOrganizationWalletEntries,
  type OrganizationWalletEntry,
  type WalletEntryType,
} from '@/api/wallet';
import { ApiProblem } from '@/api/request';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useOrganizationScope } from '@/pages/identity/useOrganizationScope';
import { useAuthStore } from '@/stores/authStore';
import {
  formatMoneyCny,
  formatShanghaiTime,
} from '@/utils/decimal';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';
import {
  WalletEntryTypeTag,
  WalletMoneyDelta,
  WalletSource,
  walletEntryTypeLabels,
} from './WalletEntryPresentation';

interface WalletSearchParams {
  organizationUserUid?: string;
  entryType?: WalletEntryType;
  occurredRange?: [string, string];
  sourceNo?: string;
}

interface PageSnapshot {
  page: number;
  itemCount: number;
  asOf: string;
}

const USER_UID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const entryTypeValueEnum = Object.fromEntries(
  Object.entries(walletEntryTypeLabels).map(([value, text]) => [
    value,
    { text },
  ]),
);

function requestErrorMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '钱包流水加载失败';
}

export default function WalletEntriesPage() {
  const scope = useDirectoryScope();
  const organizationScope = useOrganizationScope(scope);
  const [searchParams] = useSearchParams();
  const actionRef = useRef<ActionType>(null);
  const cursorByPage = useRef<Map<number, string | undefined>>(
    new Map([[1, undefined]]),
  );
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [paginationTotal, setPaginationTotal] = useState(0);
  const [pageSnapshot, setPageSnapshot] = useState<PageSnapshot | null>(null);
  const [tableError, setTableError] = useState<string>();
  const canReadDelivery = useAuthStore((state) =>
    state.hasCapability('delivery.read'));
  const organizationCode = organizationScope.organizationCode;
  const linkedOrganizationUserUid =
    searchParams.get('organizationUserUid')?.trim() || undefined;
  const linkedSourceNo =
    searchParams.get('sourceNo')?.trim() || undefined;

  const resetCursorNavigation = useCallback(() => {
    cursorByPage.current = new Map([[1, undefined]]);
    setCurrentPage(1);
    setPaginationTotal(0);
    setPageSnapshot(null);
  }, []);

  useEffect(() => {
    resetCursorNavigation();
    setTableError(undefined);
  }, [organizationCode, resetCursorNavigation, scope.context]);

  const columns: ProColumns<OrganizationWalletEntry>[] = [
    {
      title: '机构用户',
      dataIndex: 'organizationUserUid',
      width: 285,
      render: (_, entry) => (
        <Typography.Text copyable>
          {entry.organizationUserUid}
        </Typography.Text>
      ),
    },
    {
      title: '流水类型',
      dataIndex: 'entryType',
      valueType: 'select',
      valueEnum: entryTypeValueEnum,
      width: 150,
      render: (_, entry) => (
        <WalletEntryTypeTag type={entry.entryType} />
      ),
    },
    {
      title: '可提现变动',
      dataIndex: 'availableDeltaYuan',
      search: false,
      width: 135,
      align: 'right',
      render: (_, entry) => (
        <WalletMoneyDelta value={entry.availableDeltaYuan} />
      ),
    },
    {
      title: '处理中变动',
      dataIndex: 'processingDeltaYuan',
      search: false,
      width: 135,
      align: 'right',
      render: (_, entry) => (
        <WalletMoneyDelta value={entry.processingDeltaYuan} />
      ),
    },
    {
      title: '变动后余额',
      dataIndex: 'availableBalanceAfterYuan',
      search: false,
      width: 135,
      align: 'right',
      render: (_, entry) =>
        `¥ ${formatMoneyCny(entry.availableBalanceAfterYuan)}`,
    },
    {
      title: '变动后处理中',
      dataIndex: 'withdrawalProcessingAfterYuan',
      search: false,
      width: 145,
      align: 'right',
      render: (_, entry) =>
        `¥ ${formatMoneyCny(entry.withdrawalProcessingAfterYuan)}`,
    },
    {
      title: '来源单号',
      dataIndex: 'sourceNo',
      width: 250,
      render: (_, entry) => (
        <WalletSource
          sourceType={entry.sourceType}
          sourceNo={entry.sourceNo}
          tenantCode={scope.platform ? scope.tenantCode : undefined}
          organizationCode={organizationCode ?? ''}
          canReadDelivery={canReadDelivery}
        />
      ),
    },
    {
      title: '发生时间',
      dataIndex: 'occurredAt',
      search: false,
      width: 190,
      render: (_, entry) => formatShanghaiTime(entry.occurredAt),
    },
    {
      title: '发生时间',
      dataIndex: 'occurredRange',
      valueType: 'dateTimeRange',
      hideInTable: true,
    },
    {
      title: '钱包内序号',
      dataIndex: 'entrySequenceNo',
      search: false,
      width: 120,
    },
    {
      title: '流水标识',
      dataIndex: 'entryUid',
      search: false,
      width: 285,
      render: (_, entry) => (
        <Typography.Text copyable>
          {entry.entryUid}
        </Typography.Text>
      ),
    },
  ];

  const content = (() => {
    if (scope.loading || organizationScope.loading) {
      return (
        <div style={{ padding: '48px 0', textAlign: 'center' }}>
          <Spin tip="正在加载钱包查询范围" />
        </div>
      );
    }
    if (!scope.context) {
      return <Empty description="请选择目标租户" />;
    }
    if (!organizationScope.organizationOptions.length) {
      return <Empty description="当前账号没有可查询的钱包机构范围" />;
    }
    if (!organizationCode) {
      return (
        <div style={{ padding: '48px 0', textAlign: 'center' }}>
          <Spin tip="正在应用机构范围" />
        </div>
      );
    }

    return (
      <>
        {tableError && (
          <Alert
            showIcon
            type="error"
            message="钱包流水加载失败"
            description={tableError}
            style={{ marginBottom: 16 }}
          />
        )}
        <ProTable<OrganizationWalletEntry, WalletSearchParams>
          {...proTableConfig}
          actionRef={actionRef}
          rowKey="entryUid"
          columns={columns}
          scroll={{ x: 1700 }}
          columnsState={{
            persistenceKey: 'ecobin.web.columns.wallet-entries.v1',
            persistenceType: 'localStorage',
            defaultValue: {
              entrySequenceNo: { show: false },
              entryUid: { show: false },
            },
          }}
          form={{
            initialValues: {
              organizationUserUid: linkedOrganizationUserUid,
              sourceNo: linkedSourceNo,
            },
          }}
          params={{
            organizationUserUid: linkedOrganizationUserUid,
            sourceNo: linkedSourceNo,
          }}
          beforeSearchSubmit={(params) => {
            resetCursorNavigation();
            return params;
          }}
          headerTitle={(
            <Space wrap>
              <WalletOutlined />
              <Typography.Text strong>目标机构</Typography.Text>
              <Select
                aria-label="目标机构"
                showSearch
                optionFilterProp="label"
                style={{ width: 340 }}
                value={organizationCode}
                options={organizationScope.organizationOptions}
                onChange={organizationScope.setOrganizationCode}
              />
              {pageSnapshot && (
                <Typography.Text type="secondary">
                  账本快照 {formatShanghaiTime(pageSnapshot.asOf)}
                  {' · '}
                  第 {pageSnapshot.page} 页，本页 {pageSnapshot.itemCount} 条
                </Typography.Text>
              )}
            </Space>
          )}
          request={async (params) => {
            if (!scope.context || !organizationCode) {
              return { data: [], total: 0, success: true };
            }
            const requestedPage = params.current ?? currentPage;
            const requestedLimit = params.pageSize ?? pageSize;
            const cursor = cursorByPage.current.get(requestedPage);
            if (requestedPage > 1 && !cursor) {
              return { data: [], total: 0, success: false };
            }
            const userUid =
              typeof params.organizationUserUid === 'string'
                ? params.organizationUserUid.trim() || undefined
                : undefined;
            if (userUid && !USER_UID.test(userUid)) {
              setTableError('机构用户标识必须是 UUIDv4');
              return { data: [], total: 0, success: false };
            }
            const occurredRange = params.occurredRange as
              | [string, string]
              | undefined;
            try {
              setTableError(undefined);
              const page = await listOrganizationWalletEntries(
                scope.context,
                organizationCode,
                {
                  organizationUserUid: userUid,
                  entryType: params.entryType as WalletEntryType | undefined,
                  occurredFrom: occurredRange?.[0]
                    ? dayjs(occurredRange[0]).toISOString()
                    : undefined,
                  occurredTo: occurredRange?.[1]
                    ? dayjs(occurredRange[1]).toISOString()
                    : undefined,
                  sourceNo:
                    typeof params.sourceNo === 'string'
                      ? params.sourceNo.trim() || undefined
                      : undefined,
                  cursor,
                  limit: requestedLimit,
                },
              );

              for (const pageNumber of cursorByPage.current.keys()) {
                if (pageNumber > requestedPage + 1) {
                  cursorByPage.current.delete(pageNumber);
                }
              }
              if (page.nextCursor) {
                cursorByPage.current.set(
                  requestedPage + 1,
                  page.nextCursor,
                );
              } else {
                cursorByPage.current.delete(requestedPage + 1);
              }
              const total = page.nextCursor
                ? requestedPage * requestedLimit + 1
                : (requestedPage - 1) * requestedLimit + page.items.length;
              setPaginationTotal(total);
              setPageSnapshot({
                page: requestedPage,
                itemCount: page.items.length,
                asOf: page.asOf,
              });
              return {
                data: page.items,
                total,
                success: true,
              };
            } catch (error) {
              setTableError(requestErrorMessage(error));
              return { data: [], total: 0, success: false };
            }
          }}
          pagination={{
            current: currentPage,
            pageSize,
            total: paginationTotal,
            showQuickJumper: false,
            pageSizeOptions: [20, 50, 100],
            showTotal: () =>
              pageSnapshot
                ? `第 ${pageSnapshot.page} 页 · 当前 ${pageSnapshot.itemCount} 条`
                : '',
            onChange: (nextPage, nextPageSize) => {
              if (nextPageSize !== pageSize) {
                cursorByPage.current = new Map([[1, undefined]]);
                setPageSize(nextPageSize);
                setCurrentPage(1);
                setPaginationTotal(0);
                return;
              }
              setCurrentPage(nextPage);
            },
          }}
        />
      </>
    );
  })();

  return (
    <PageContainer
      {...pageHeader(
        '钱包流水',
        '只展示已实际改变钱包投影的追加账本事实；待审核返现不提前伪造流水。',
      )}
    >
      <DirectoryScopeBar scope={scope} />
      {content}
    </PageContainer>
  );
}

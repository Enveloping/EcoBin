import { useEffect, useRef, useState } from 'react';
import {
  PageContainer,
  ProTable,
  type ActionType,
  type ProColumns,
} from '@ant-design/pro-components';
import {
  Alert,
  Button,
  Card,
  InputNumber,
  Popconfirm,
  QRCode,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  CopyOutlined,
  DeleteOutlined,
  FileExcelOutlined,
  FilePdfOutlined,
  KeyOutlined,
  PrinterOutlined,
  QrcodeOutlined,
  SafetyCertificateOutlined,
  TagsOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import QrCodeEncoder from 'qrcode';
import {
  createBagLabelBatch,
  deleteBagLabelBatch,
  getBagLabelBatch,
  listBagLabelBatches,
  type BagLabelBatch,
  type BagLabelBatchSummary,
} from '@/api/bagLabels';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { formatShanghaiTime } from '@/utils/decimal';
import { pageHeader, proTableConfig } from '@/utils/pageStyle';
import { MAX_BAG_LABEL_BATCH_QUANTITY } from './bagLabelLimits.ts';
import './bag-labels.css';

interface PrintableLabel {
  sequenceNo: number;
  bagCode: string;
  qrDataUrl: string;
}

interface PrintJob {
  batchUid: string;
  keyId: string;
  labels: PrintableLabel[];
}

type ExcelExportKind = 'bag-code' | 'qr-code';

interface ExcelExportJob {
  batchUid: string;
  kind: ExcelExportKind;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '袋码操作失败';
}

function pagesOf<T>(items: T[], size: number): T[][] {
  const pages: T[][] = [];
  for (let index = 0; index < items.length; index += size) {
    pages.push(items.slice(index, index + size));
  }
  return pages;
}

export default function BagLabelsPage() {
  const actionRef = useRef<ActionType>(null);
  const executeCommand = useCommandExecutor();
  const [quantity, setQuantity] = useState(24);
  const [creating, setCreating] = useState(false);
  const [details, setDetails] = useState<Record<string, BagLabelBatch>>({});
  const [loadingDetails, setLoadingDetails] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<readonly string[]>([]);
  const [printingUid, setPrintingUid] = useState<string>();
  const [printJob, setPrintJob] = useState<PrintJob>();
  const [excelExportJob, setExcelExportJob] = useState<ExcelExportJob>();

  const loadDetail = async (batchUid: string): Promise<BagLabelBatch> => {
    const cached = details[batchUid];
    if (cached) return cached;
    setLoadingDetails((current) => new Set(current).add(batchUid));
    try {
      const detail = await getBagLabelBatch(batchUid);
      setDetails((current) => ({ ...current, [batchUid]: detail }));
      return detail;
    } catch (error) {
      message.error(errorMessage(error));
      throw error;
    } finally {
      setLoadingDetails((current) => {
        const next = new Set(current);
        next.delete(batchUid);
        return next;
      });
    }
  };

  const generate = async () => {
    if (!Number.isInteger(quantity)
        || quantity < 1
        || quantity > MAX_BAG_LABEL_BATCH_QUANTITY) {
      message.warning(
        `每批只能生成 1 到 ${MAX_BAG_LABEL_BATCH_QUANTITY} 个袋码`,
      );
      return;
    }
    const payload = { quantity };
    setCreating(true);
    try {
      const created = await executeCommand(
        commandKey('bag-label-batch.generate', 'new', payload),
        (intent) => createBagLabelBatch(payload, intent),
      );
      setDetails((current) => ({
        ...current,
        [created.batchUid]: created,
      }));
      setExpanded([created.batchUid]);
      await actionRef.current?.reload();
      message.success(`已生成 ${created.quantity} 个防伪袋码`);
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setCreating(false);
    }
  };

  const remove = async (batchUid: string) => {
    try {
      await deleteBagLabelBatch(batchUid);
      setDetails((current) => {
        const next = { ...current };
        delete next[batchUid];
        return next;
      });
      setExpanded((current) => current.filter((uid) => uid !== batchUid));
      await actionRef.current?.reload();
      message.success('批次打印记录已删除');
    } catch (error) {
      message.error(errorMessage(error));
    }
  };

  const printBatch = async (batchUid: string) => {
    setPrintingUid(batchUid);
    try {
      const detail = await loadDetail(batchUid);
      const labels = await Promise.all(detail.labels.map(async (label) => ({
        sequenceNo: label.sequenceNo,
        bagCode: label.bagCode,
        qrDataUrl: await QrCodeEncoder.toDataURL(label.qrPayload, {
          width: 420,
          margin: 1,
          errorCorrectionLevel: 'M',
          color: { dark: '#101914', light: '#ffffff' },
        }),
      })));
      setPrintJob({
        batchUid: detail.batchUid,
        keyId: detail.keyId,
        labels,
      });
    } catch (error) {
      if (!(error instanceof ApiProblem)) {
        message.error(errorMessage(error));
      }
    } finally {
      setPrintingUid(undefined);
    }
  };

  const exportExcel = async (
    batchUid: string,
    kind: ExcelExportKind,
  ) => {
    setExcelExportJob({ batchUid, kind });
    try {
      const detail = await loadDetail(batchUid);
      // ExcelJS 体积较大，低频导出时才加载，不进入页面常规代码包。
      const exporter = await import('./excelExport.ts');
      if (kind === 'bag-code') {
        await exporter.downloadBagCodeExcel(detail);
        message.success('袋码内容 Excel 已导出');
      } else {
        await exporter.downloadBagQrExcel(detail);
        message.success('二维码 Excel 已导出');
      }
    } catch (error) {
      if (!(error instanceof ApiProblem)) {
        message.error(errorMessage(error));
      }
    } finally {
      setExcelExportJob(undefined);
    }
  };

  useEffect(() => {
    if (!printJob) return undefined;
    const timer = window.setTimeout(() => {
      window.print();
      setPrintJob(undefined);
    }, 180);
    return () => window.clearTimeout(timer);
  }, [printJob]);

  const columns: ProColumns<BagLabelBatchSummary>[] = [
    {
      title: '生成时间',
      dataIndex: 'createdAt',
      width: 190,
      render: (_, row) => formatShanghaiTime(row.createdAt),
    },
    {
      title: '批次',
      dataIndex: 'batchUid',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text className="bag-label-batch-id" copyable>
            {row.batchUid}
          </Typography.Text>
          <Typography.Text type="secondary">
            {row.quantity} 张标签
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '签发密钥',
      dataIndex: 'keyId',
      width: 120,
      render: (_, row) => <Tag icon={<KeyOutlined />}>{row.keyId}</Tag>,
    },
    {
      title: '操作人',
      dataIndex: ['createdBy', 'displayName'],
      width: 170,
      render: (_, row) => row.createdBy.displayName,
    },
    {
      title: '操作',
      valueType: 'option',
      width: 510,
      render: (_, row) => [
        <Button
          key="print"
          type="link"
          icon={<PrinterOutlined />}
          loading={printingUid === row.batchUid}
          onClick={() => void printBatch(row.batchUid)}
        >
          打印 / 保存 PDF
        </Button>,
        <Button
          key="bag-code-excel"
          type="link"
          icon={<FileExcelOutlined />}
          loading={excelExportJob?.batchUid === row.batchUid
            && excelExportJob.kind === 'bag-code'}
          onClick={() => void exportExcel(row.batchUid, 'bag-code')}
        >
          导出袋码 Excel
        </Button>,
        <Button
          key="qr-code-excel"
          type="link"
          icon={<QrcodeOutlined />}
          loading={excelExportJob?.batchUid === row.batchUid
            && excelExportJob.kind === 'qr-code'}
          onClick={() => void exportExcel(row.batchUid, 'qr-code')}
        >
          导出二维码 Excel
        </Button>,
        <Popconfirm
          key="delete"
          title="删除这批打印记录？"
          description="已经印出的袋码仍然有效；删除后平台不能再查看或补打本批次。"
          okText="删除记录"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onConfirm={() => remove(row.batchUid)}
        >
          <Button type="link" danger icon={<DeleteOutlined />}>
            删除
          </Button>
        </Popconfirm>,
      ],
    },
  ];

  const expandedRow = (row: BagLabelBatchSummary) => {
    if (loadingDetails.has(row.batchUid)) {
      return <div className="bag-label-loading"><Spin tip="读取袋码…" /></div>;
    }
    const detail = details[row.batchUid];
    if (!detail) {
      return (
        <Alert
          type="warning"
          showIcon
          message="袋码明细暂未读取"
          action={(
            <Button size="small" onClick={() => void loadDetail(row.batchUid)}>
              重新读取
            </Button>
          )}
        />
      );
    }
    return (
      <div className="bag-label-grid">
        {detail.labels.map((label) => (
          <Card key={label.sequenceNo} size="small" className="bag-label-card">
            <div className="bag-label-card-sequence">
              <span>{String(label.sequenceNo).padStart(3, '0')}</span>
              <CopyOutlined />
            </div>
            <QRCode
              value={label.qrPayload}
              size={116}
              bordered={false}
              errorLevel="M"
              color="#101914"
            />
            <Typography.Text
              className="bag-label-code"
              copyable={{ text: label.bagCode, tooltips: ['复制袋码', '已复制'] }}
            >
              {label.bagCode}
            </Typography.Text>
          </Card>
        ))}
      </div>
    );
  };

  return (
    <PageContainer
      header={pageHeader(
        '袋码管理',
        '签发可验真的实体清运袋标签，并按批次打印、保存 PDF 或导出 Excel。',
      )}
    >
      <section className="bag-label-workbench">
        <div className="bag-label-workbench-copy">
          <div className="bag-label-eyebrow">
            <SafetyCertificateOutlined /> AUTHENTICATED LABEL STATION
          </div>
          <Typography.Title level={2}>先签发，再贴袋</Typography.Title>
          <Typography.Paragraph>
            每个二维码都带有平台防伪签名。设备登记和清运换袋时，后端会验证签名；
            仅仿造相同文字格式不能通过。
          </Typography.Paragraph>
          <Space wrap size={18} className="bag-label-facts">
            <span>
              <TagsOutlined /> 每批 1–{MAX_BAG_LABEL_BATCH_QUANTITY} 张
            </span>
            <span><FilePdfOutlined /> A4 · 3 × 8</span>
            <span><KeyOutlined /> 后端活动密钥签发</span>
          </Space>
        </div>
        <div className="bag-label-generator">
          <Typography.Text type="secondary">本批标签数量</Typography.Text>
          <div className="bag-label-generator-row">
            <InputNumber
              aria-label="本批标签数量"
              min={1}
              max={MAX_BAG_LABEL_BATCH_QUANTITY}
              precision={0}
              value={quantity}
              onChange={(value) => setQuantity(Number(value) || 1)}
            />
            <Button
              type="primary"
              size="large"
              icon={<ThunderboltOutlined />}
              loading={creating}
              onClick={() => void generate()}
            >
              生成袋码
            </Button>
          </div>
          <Typography.Text className="bag-label-generator-note">
            建议按 24 的倍数生成，正好铺满整张 A4 标签纸。
          </Typography.Text>
        </div>
      </section>

      <Alert
        className="bag-label-boundary"
        type="info"
        showIcon
        message="删除的是平台打印记录，不是已经贴出的袋码"
        description="袋码无需提前分配租户或机构。机构首次用一个有效新袋码发起清运时，系统才在当前机构下建立袋资产。"
      />

      <ProTable<BagLabelBatchSummary>
        {...proTableConfig}
        actionRef={actionRef}
        rowKey="batchUid"
        columns={columns}
        search={false}
        headerTitle="签发批次"
        request={async (params) => {
          try {
            const page = await listBagLabelBatches({
              page: params.current,
              pageSize: params.pageSize,
            });
            return { data: page.items, total: page.total, success: true };
          } catch (error) {
            message.error(errorMessage(error));
            return { data: [], total: 0, success: false };
          }
        }}
        pagination={{ defaultPageSize: 20, showSizeChanger: true }}
        expandable={{
          expandedRowKeys: expanded,
          expandedRowRender: expandedRow,
          onExpand: (open, row) => {
            setExpanded((current) => open
              ? [...new Set([...current, row.batchUid])]
              : current.filter((uid) => uid !== row.batchUid));
            if (open && !details[row.batchUid]) {
              void loadDetail(row.batchUid);
            }
          },
        }}
      />

      {printJob && (
        <div className="bag-label-print-root" aria-hidden="true">
          {pagesOf(printJob.labels, 24).map((labels, pageIndex) => (
            <section className="bag-label-print-page" key={pageIndex}>
              {labels.map((label) => (
                <article className="bag-label-print-item" key={label.sequenceNo}>
                  <img src={label.qrDataUrl} alt="" />
                  <div className="bag-label-print-copy">
                    <strong>EcoBin 清运袋</strong>
                    <span className="bag-label-print-meta">
                      {printJob.keyId} · {String(label.sequenceNo).padStart(3, '0')}
                    </span>
                    <code>{label.bagCode}</code>
                    <small>批次 {printJob.batchUid.slice(0, 8)}</small>
                  </div>
                </article>
              ))}
            </section>
          ))}
        </div>
      )}
    </PageContainer>
  );
}

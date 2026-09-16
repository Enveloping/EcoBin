import { Space, Tag, Tooltip, Typography } from 'antd';
import type { CSSProperties } from 'react';
import type { DeviceAsset } from '@/api/deviceDirectory';
import { formatShanghaiTime } from '@/utils/decimal';
import { deviceManagementSummary } from './deviceManagementPresentation';
import './device-list.css';

type Port = NonNullable<DeviceAsset['listStatus']>['ports'][number];
type PortMetric = 'weight' | 'weightFull' | 'infraredFull';
type MetricTone = 'success' | 'danger' | 'secondary' | undefined;

interface MetricValue {
  text: string;
  tone: MetricTone;
  tip: string;
}

export const listAcceptanceLabels: Record<string, string> = {
  PENDING: '待验收', FAILED: '未通过', PASSED: '已通过',
};

function shortIdentifier(value: string) {
  return value.length > 8 ? `${value.slice(0, 8)}…` : value;
}

export function DeviceIdentifier({ asset, onOpen }: { asset: DeviceAsset; onOpen: () => void }) {
  return <Space direction="vertical" size={1}>
    <Tooltip title={`设备序列号：${asset.hardwareSn}`} trigger={['hover', 'focus']}>
      <Typography.Link strong onClick={onOpen} tabIndex={0} role="button"
        aria-label={`打开设备 ${asset.hardwareSn}`} onKeyDown={event => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onOpen(); }
        }}>
        {shortIdentifier(asset.hardwareSn)}
      </Typography.Link>
    </Tooltip>
    <Tooltip title={`设备编号：${asset.deviceCode}`} trigger={['hover', 'focus']}>
      <Typography.Text type="secondary" tabIndex={0} copyable={{ text: asset.deviceCode }}>
        {shortIdentifier(asset.deviceCode)}
      </Typography.Text>
    </Tooltip>
  </Space>;
}

function observedAt(time: string | null | undefined) {
  return time ? `采集于 ${formatShanghaiTime(time)}` : '尚未收到有效上报';
}

export function DeviceCondition({ asset }: { asset: DeviceAsset }) {
  const management = deviceManagementSummary(asset);
  const managementAbnormal = management?.businessAdmission === 'PAUSED'
    || management?.compatibility === 'INCOMPATIBLE';
  const reason = managementAbnormal ? management?.primaryReason ?? null : null;
  const faults = [...new Set([
    ...(reason ? [reason.title] : []),
    ...(asset.listStatus?.faults ?? []),
    ...(asset.listStatus?.ports ?? []).flatMap(port => port.faults.map(fault => `${port.portNo} 口：${fault}`)),
  ])];
  const abnormal = managementAbnormal || faults.length > 0;
  return <Tooltip
    title={abnormal ? '设备存在明确异常，进入详情查看具体原因' : '未发现明确异常'}
    trigger={['hover', 'focus']}
  >
    <Tag color={abnormal ? 'error' : 'success'} tabIndex={0} className="device-list-condition">
      {abnormal ? '异常' : '正常'}
    </Tag>
  </Tooltip>;
}

function metricValue(port: Port | undefined, metric: PortMetric): MetricValue {
  if (metric === 'weightFull') {
    const full = port?.weightFull;
    return { text: full == null ? '未上报' : full ? '已满' : '未满',
      tone: full == null ? 'secondary' : full ? 'danger' : 'success',
      tip: `当前袋最近一次重量判断；${observedAt(port?.fullnessObservedAt)}` };
  }
  if (metric === 'infraredFull') {
    const known = port?.infraredSensorHealth === 'OK' && !!port.observedAt;
    const full = known && port.infraredValue === 'BLOCKED' ? true
      : known && port.infraredValue === 'CLEAR' ? false : null;
    const fault = port?.infraredSensorHealth && !['OK', 'UNKNOWN'].includes(port.infraredSensorHealth);
    return { text: full == null ? fault ? '故障' : '未知' : full ? '已满' : '未满',
      tone: fault || full === true ? 'danger' : full === false ? 'success' : 'secondary',
      tip: `红外遮挡观测；遮挡显示已满，无遮挡显示未满。${observedAt(port?.observedAt)}` };
  }
  const known = port?.weightValueAvailable === true && port.weightSensorHealth === 'OK'
    && port.observedAt
    && Number.isSafeInteger(port.reportedWeightGrams);
  const fault = port?.weightSensorHealth && !['OK', 'UNKNOWN'].includes(port.weightSensorHealth);
  const overallFull = port?.overallFull;
  return {
    text: known
      ? `${(port!.reportedWeightGrams! / 1000).toLocaleString('zh-CN', { maximumFractionDigits: 3 })} kg`
      : fault ? '故障' : '未知',
    tone: fault ? 'danger' : !known ? 'secondary'
      : overallFull === true ? 'danger' : overallFull === false ? 'success' : undefined,
    tip: `设备称重读数（含袋）；综合满溢${overallFull == null ? '尚未上报' : overallFull ? '为已满' : '为未满'}${port?.weightMeasurementStatus === 'UNSTABLE' ? '，读数未稳定' : ''}；${observedAt(port?.observedAt)}`,
  };
}

export function DevicePortMetric({ asset, metric }: { asset: DeviceAsset; metric: PortMetric }) {
  const reported = asset.listStatus?.ports ?? [];
  const numbers = [...new Set([
    ...Array.from({ length: Math.min(6, Math.max(1, asset.expectedPortCount)) }, (_, index) => index + 1),
    ...reported.map(port => port.portNo),
  ])].sort((a, b) => a - b);
  const gridStyle = { '--device-port-count': numbers.length } as CSSProperties;
  return <div className="device-list-ports" style={gridStyle}>
    {numbers.map(portNo => {
      const port = reported.find(item => item.portNo === portNo);
      const value = metricValue(port, metric);
      return <Tooltip key={portNo} title={`${port?.displayName ?? `投口 ${portNo}`} · ${value.tip}`} trigger={['hover', 'focus']}>
        <div className="device-list-port" tabIndex={0} data-port-no={portNo}>
          <span className="device-list-port-label">{portNo} 口</span>
          <Typography.Text type={value.tone}>
            {value.text}
          </Typography.Text>
        </div>
      </Tooltip>;
    })}
  </div>;
}

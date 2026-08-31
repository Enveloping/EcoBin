import type { DeviceFactoryProgress } from '@/api/deviceDirectory';

export interface FactoryFailureGuidance {
  title: string;
  action: string;
}

export type FactoryProgressStepStatus =
  | 'wait'
  | 'process'
  | 'finish'
  | 'error';

export interface FactoryProgressStep {
  key: string;
  title: string;
  description: string;
  status: FactoryProgressStepStatus;
}

const FAILURE_GUIDANCE: Record<string, FactoryFailureGuidance> = {
  DEVICE_ASSET_UNAVAILABLE: {
    title: '设备资产当前不可用于出厂接入',
    action: '检查设备是否被禁用或报废；只有恢复为正常生命周期状态后才能继续接入。',
  },
  ONENET_NOT_ONLINE: {
    title: 'OneNet 当前不在线',
    action: '检查物联网卡、蜂窝信号、系统时间和 MQTT 连接，恢复在线后重新读取验收证据。',
  },
  FACTORY_BAGS_INCOMPLETE: {
    title: '厂家初始袋码尚未登记完整',
    action: '在厂家小程序中扫描设备及每个投口的初始袋码，确认已登记数量等于投口数量。',
  },
  UNSUPPORTED_EDGE_SOFTWARE: {
    title: '香橙派软件版本不在验收白名单',
    action: '核对候选镜像版本，安装当前批准的软件负载后重新发起机器验收。',
  },
  UNSUPPORTED_EDGE_PROTOCOL: {
    title: '设备协议版本不受后端支持',
    action: '核对镜像与当前 OneNet 契约版本，升级到配套版本后重新验收。',
  },
  PERSISTENT_STORE_UNHEALTHY: {
    title: '设备持久化存储不健康',
    action: '检查 TF 卡、SQLite 文件权限和磁盘剩余空间；修复后重启正式硬件服务并重新验收。',
  },
  CONFIGURATION_PERSISTENCE_UNHEALTHY: {
    title: '设备配置未可靠持久化',
    action: '检查本地配置文件、摘要和写盘错误；确认配置可在重启后恢复，再重新验收。',
  },
  MCU_COMMUNICATION_UNHEALTHY: {
    title: 'MCU 串口通信不健康',
    action: '检查 RX、TX、GND、串口配置和 MCU 供电，确认固定帧请求能够正常收发。',
  },
  SENSOR_SELF_TEST_FAILED: {
    title: '传感器自检未通过',
    action: '在设备侧检查称重、红外和烟感的自检结果及接线，排除故障后重新采集证据。',
  },
  PORT_COUNT_MISMATCH: {
    title: '验收投口数量与资产配置不一致',
    action: '核对设备型号、资产投口数和 MCU 返回的投口数量，修正不一致后重新验收。',
  },
  SENSOR_EVIDENCE_DIGEST_EMPTY: {
    title: '传感器证据摘要为空',
    action: '检查 P8 证据采集和摘要计算，确认真实采样已写入证据后重新验收。',
  },
  CAMERA_CAPTURE_FAILED: {
    title: '摄像头采集失败',
    action: '检查摄像头供电、USB 枚举、设备路径和占用情况，确认两路摄像头均可拍照。',
  },
  CAMERA_COUNT_INSUFFICIENT: {
    title: '可用摄像头数量不足',
    action: '确认内外两路摄像头均已接入并被当前配置识别，再重新采集证据。',
  },
  CAMERA_CAPTURE_DIGEST_EMPTY: {
    title: '摄像头采集证据摘要为空',
    action: '检查照片采集结果和摘要计算，确保验收照片已可靠保存后重新验收。',
  },
  CAMERA_UPLOAD_READBACK_FAILED: {
    title: 'COS 上传或读回校验失败',
    action: '检查设备联网、COS 临时授权、对象上传和 HEAD 读回结果，修复后重新验收。',
  },
  CAMERA_UPLOAD_DIGEST_EMPTY: {
    title: '摄像头上传证据摘要为空',
    action: '检查上传成功后的摘要落盘和 P8 证据组装，补齐后重新验收。',
  },
  DEVICE_ENTRY_URL_NOT_STORED: {
    title: '设备入口 URL 未可靠保存',
    action: '重新同步设备入口 URL，确认香橙派本地写盘并能在重启后读取。',
  },
  DEVICE_ENTRY_URL_SHA256_MISMATCH: {
    title: '设备入口 URL 摘要不一致',
    action: '重新下发当前设备的入口 URL，清除错误的本地值并核对摘要后重新验收。',
  },
  ACCEPTANCE_EVIDENCE_MISSING: {
    title: '当前验收代次缺少设备证据',
    action: '检查 P8 请求是否到达设备、证据发件箱和 OneNet 上行，恢复后等待可靠任务收敛。',
  },
  MACHINE_ACCEPTANCE_FAILED: {
    title: '机器验收未通过但缺少具体失败项',
    action: '重新读取当前代次的验收证据；若仍无具体失败码，请携带设备 SN 和验收代次排查后端日志。',
  },
  ACCEPTANCE_REQUEST_BLOCKED: {
    title: 'P8 请求可靠任务已阻断',
    action: '打开对应可靠任务，依据最近尝试的外部请求号和脱敏诊断排除阻断后再恢复任务。',
  },
  ACCEPTANCE_REQUEST_CANCELLED: {
    title: 'P8 请求可靠任务已取消',
    action: '核对当前袋码修订和验收代次，确认事实仍有效后重新读取验收证据以创建新请求。',
  },
  FACTORY_SEAL_AUTHORIZATION_REJECTED: {
    title: '设备拒绝了封存授权',
    action: '核对验收代次、权威证据摘要和设备当前状态；修复不一致后由可靠任务重新收敛。',
  },
  DEVICE_REJECTED_AUTHORIZATION: {
    title: '设备拒绝了封存授权',
    action: '查看设备返回的拒绝码，核对验收代次和证据绑定；不要手工跳过封存校验。',
  },
  ACCEPTANCE_EVIDENCE_NOT_LATEST: {
    title: '封存授权绑定的证据已经不是最新证据',
    action: '先核对后来证据及当前权威验收结论，再按最新验收代次生成封存授权。',
  },
  AUTHORIZATION_FACT_MISSING: {
    title: '封存授权事实缺失',
    action: '检查当前验收代次的封存授权记录和可靠任务，不要直接在设备侧强制封存。',
  },
  FACTORY_SEAL_AUTHORIZATION_CANCELLED: {
    title: '当前封存授权已取消',
    action: '查看取消原因并核对最新验收证据；不得继续使用已经取消的授权结束出厂模式。',
  },
  FACTORY_SEAL_TASK_BLOCKED: {
    title: '封存授权可靠任务已阻断',
    action: '打开对应可靠任务，依据最近尝试诊断恢复 OneNet 下行或设备受理条件。',
  },
  FACTORY_SEAL_TASK_CANCELLED: {
    title: '封存授权可靠任务已取消',
    action: '核对权威证据和当前验收代次，确认仍为 PASSED 后重新读取验收证据。',
  },
  FACTORY_SEAL_STATUS_UNKNOWN: {
    title: '后端无法识别当前封存状态',
    action: '停止人工封存操作，记录设备 SN、验收代次和当前状态并联系技术支持。',
  },
};

export function factoryFailureGuidance(code: string): FactoryFailureGuidance {
  return FAILURE_GUIDANCE[code] ?? {
    title: `未识别的问题代码：${code}`,
    action: '记录完整问题代码、设备硬件 SN 和发生时间，到可靠任务详情及设备日志中继续定位。',
  };
}

function progressedPastSealIssue(progress: DeviceFactoryProgress): boolean {
  return progress.seal.status === 'ACKNOWLEDGED'
    || progress.seal.status === 'SEALED';
}

export function buildFactoryProgressSteps(
  progress: DeviceFactoryProgress,
): FactoryProgressStep[] {
  const hasCurrentEvidence = Boolean(
    progress.acceptance.authoritativeEvidence,
  );
  const p8Requested = Boolean(progress.acceptanceRequest.taskUid)
    || hasCurrentEvidence;
  const p8Blocked = progress.acceptanceRequest.taskState === 'BLOCKED'
    || progress.acceptanceRequest.taskState === 'CANCELLED';
  const sealBlocked = progress.seal.taskState === 'BLOCKED'
    || progress.seal.taskState === 'CANCELLED'
    || progress.seal.status === 'CANCELLED';
  const sealAccepted = progressedPastSealIssue(progress);
  const sealed = progress.seal.status === 'SEALED';

  return [
    {
      key: 'factory-bags',
      title: '初始袋码',
      description: progress.factoryBags.complete
        ? `${progress.factoryBags.verifiedCount}/${progress.factoryBags.expectedPortCount}`
        : `已登记 ${progress.factoryBags.verifiedCount}/${progress.factoryBags.expectedPortCount}`,
      status: progress.factoryBags.complete ? 'finish' : 'process',
    },
    {
      key: 'acceptance-request',
      title: 'P8 请求',
      description: p8Blocked
        ? '可靠任务阻断'
        : hasCurrentEvidence
          ? '证据已到达'
          : p8Requested
            ? '已发起'
            : progress.factoryBags.complete
              ? '等待系统发起'
              : '等待袋码完整',
      status: p8Blocked
        ? 'error'
        : hasCurrentEvidence
          ? 'finish'
          : p8Requested
            ? 'process'
            : 'wait',
    },
    {
      key: 'acceptance',
      title: '权威判定',
      description: progress.acceptance.status === 'PASSED'
        ? 'PASSED'
        : progress.acceptance.status === 'FAILED'
          ? 'FAILED'
          : hasCurrentEvidence
            ? '正在判定'
            : '等待证据',
      status: progress.acceptance.status === 'PASSED'
        ? 'finish'
        : progress.acceptance.status === 'FAILED'
          ? 'error'
          : hasCurrentEvidence
            ? 'process'
            : 'wait',
    },
    {
      key: 'seal-authorization',
      title: '封存授权',
      description: sealAccepted
        ? '设备已接收'
        : sealBlocked
          ? '授权阻断'
          : progress.seal.status === 'PENDING'
            ? '正在投递'
            : '尚未签发',
      status: sealAccepted
        ? 'finish'
        : sealBlocked
          ? 'error'
          : progress.seal.status === 'PENDING'
            ? 'process'
            : 'wait',
    },
    {
      key: 'local-confirmation',
      title: '本地确认',
      description: sealed
        ? '操作员已确认'
        : progress.seal.status === 'ACKNOWLEDGED'
          ? '等待局域网页操作'
          : '尚未开放',
      status: sealed
        ? 'finish'
        : progress.seal.status === 'ACKNOWLEDGED'
          ? 'process'
          : sealBlocked
            ? 'error'
            : 'wait',
    },
    {
      key: 'complete',
      title: '封存完成',
      description: sealed ? 'SEALED' : '等待设备上报',
      status: sealed ? 'finish' : sealBlocked ? 'error' : 'wait',
    },
  ];
}

export function factoryProgressSummary(progress: DeviceFactoryProgress): {
  type: 'success' | 'info' | 'warning' | 'error';
  message: string;
  description: string;
} {
  if (progress.seal.status === 'SEALED') {
    return {
      type: 'success',
      message: '设备已完成出厂封存',
      description: '后端已收到 FACTORY_SEAL_COMPLETED；该设备具备继续进行永久归属分配的封存条件。',
    };
  }
  if (progress.seal.status === 'ACKNOWLEDGED') {
    return {
      type: 'warning',
      message: '封存授权已被设备接受',
      description: '等待操作员连接设备局域网页并确认结束出厂模式；此状态还不代表封存完成。',
    };
  }
  if (
    progress.status === 'BLOCKED'
    || progress.acceptance.status === 'FAILED'
    || progress.acceptanceRequest.taskState === 'BLOCKED'
    || progress.seal.taskState === 'BLOCKED'
    || progress.seal.status === 'CANCELLED'
  ) {
    return {
      type: 'error',
      message: '出厂接入流程已阻断',
      description: '请按下方稳定问题代码和处置建议排除原因，不能把历史证据或任务已投递当作当前验收通过。',
    };
  }
  if (progress.acceptance.status === 'PASSED') {
    return {
      type: 'info',
      message: '机器验收已通过，正在完成封存交接',
      description: '权威 PASSED 已形成；继续等待当前代次的封存授权送达设备。',
    };
  }
  return {
    type: 'info',
    message: '出厂接入正在进行',
    description: '系统会自动推进；只有当前节点出现稳定问题代码时才需要人工处理。',
  };
}

export function collectFactoryProgressCodes(
  progress: DeviceFactoryProgress,
): string[] {
  return Array.from(new Set([
    progress.blockingCode,
    ...progress.acceptance.currentFailureReasons,
    progress.acceptanceRequest.blockedReasonCode,
    progress.seal.blockedReasonCode,
    progress.seal.cancellationReason,
  ].filter((code): code is string => Boolean(code))));
}

const STAGE_LABELS: Record<string, string> = {
  DEVICE_ASSET: '设备资产状态',
  FACTORY_BAGS: '厂家初始袋码',
  MACHINE_ACCEPTANCE: 'P8 机器验收与权威判定',
  FACTORY_SEAL_AUTHORIZATION: '封存授权投递',
  END_FACTORY_MODE: '局域网页人工确认',
  FACTORY_SEALED: '封存完成',
};

const ACTION_LABELS: Record<string, string> = {
  SCAN_FACTORY_BAGS: '使用厂家小程序补齐初始袋码',
  WAIT_FOR_DEVICE_ONLINE: '等待设备恢复 OneNet 在线',
  WAIT_FOR_ACCEPTANCE_REQUEST: '等待系统发起 P8 请求',
  WAIT_FOR_ACCEPTANCE_EVIDENCE: '等待设备采集并上传 P8 证据',
  VIEW_ACCEPTANCE_FAILURES: '查看当前权威验收失败原因',
  REEVALUATE_ACCEPTANCE: '排除故障后重新读取验收证据',
  WAIT_FOR_FACTORY_SEAL_AUTHORIZATION: '等待封存授权可靠投递',
  WAIT_FOR_FACTORY_SEAL_ACKNOWLEDGEMENT: '等待设备接受封存授权',
  CONFIRM_END_FACTORY_MODE: '连接设备局域网页，确认结束出厂模式',
  OPEN_RELIABLE_TASK: '打开对应可靠任务查看尝试记录',
  RESOLVE_ACCEPTANCE_TASK_BLOCKER: '排除 P8 请求可靠任务的阻断原因',
  RESOLVE_FACTORY_SEAL_TASK_BLOCKER: '排除封存授权可靠任务的阻断原因',
  VIEW_FACTORY_SEAL_CANCELLATION: '查看封存授权取消原因',
  RESTORE_DEVICE_ASSET: '先恢复设备资产为正常状态',
  CONTACT_SUPPORT: '记录诊断信息并联系技术支持',
};

export function factoryStageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage;
}

export function factoryActionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

export function taskStateLabel(state: string | null): string {
  if (!state) return '尚未创建';
  return ({
    PENDING: '等待执行',
    DONE: '已完成',
    CANCELLED: '已取消',
    BLOCKED: '已阻断',
  } as Record<string, string>)[state] ?? state;
}

export function sealStatusLabel(status: DeviceFactoryProgress['seal']['status']): string {
  return {
    NOT_ISSUED: '尚未签发',
    PENDING: '授权投递中',
    ACKNOWLEDGED: '设备已接受，等待局域网页结束出厂',
    CANCELLED: '授权已取消',
    SEALED: '封存完成',
  }[status];
}

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
    title: '本次检查时设备未连接云端',
    action: '检查物联网卡、蜂窝信号和设备网络；恢复在线后，系统会继续核对验收结果。',
  },
  FACTORY_BAGS_INCOMPLETE: {
    title: '厂家初始袋码尚未登记完整',
    action: '在厂家小程序中扫描设备及每个投口的初始袋码，确认已登记数量等于投口数量。',
  },
  UNSUPPORTED_EDGE_SOFTWARE: {
    title: '设备软件版本尚未获得后台认可',
    action: '安装当前批准的设备软件版本，并确认后台认可版本列表已经更新，然后重新验收。',
  },
  UNSUPPORTED_EDGE_PROTOCOL: {
    title: '设备通信版本与后台不匹配',
    action: '把设备软件升级到与当前后台配套的版本，然后重新验收。',
  },
  PERSISTENT_STORE_UNHEALTHY: {
    title: '设备本地存储异常',
    action: '检查存储卡状态和剩余空间；修复后重启设备日常服务并重新验收。',
  },
  CONFIGURATION_PERSISTENCE_UNHEALTHY: {
    title: '设备配置未能安全保存',
    action: '检查设备配置保存情况；确认设备重启后仍能读取当前配置，再重新验收。',
  },
  MCU_COMMUNICATION_UNHEALTHY: {
    title: '设备控制板通信异常',
    action: '检查控制板供电、串口收发线和地线连接，确认设备可以正常读取控制板状态。',
  },
  SENSOR_SELF_TEST_FAILED: {
    title: '传感器自检未通过',
    action: '在设备侧检查称重、红外和烟感的自检结果及接线，排除故障后重新采集检查结果。',
  },
  PORT_COUNT_MISMATCH: {
    title: '验收投口数量与资产配置不一致',
    action: '核对设备型号、后台登记投口数和控制板识别的投口数，修正不一致后重新验收。',
  },
  SENSOR_EVIDENCE_DIGEST_EMPTY: {
    title: '传感器检查记录不完整',
    action: '确认设备已经完成真实传感器采样并安全保存检查结果，然后重新验收。',
  },
  CAMERA_CAPTURE_FAILED: {
    title: '摄像头采集失败',
    action: '检查摄像头供电和连接线，确认内外两路摄像头都能正常拍照。',
  },
  CAMERA_COUNT_INSUFFICIENT: {
    title: '可用摄像头数量不足',
    action: '确认内外两路摄像头均已接入并被当前配置识别，再重新采集检查结果。',
  },
  CAMERA_CAPTURE_DIGEST_EMPTY: {
    title: '摄像头检查记录不完整',
    action: '确认两路验收照片已经拍摄并安全保存，然后重新验收。',
  },
  CAMERA_UPLOAD_READBACK_FAILED: {
    title: '验收照片上传或读取校验失败',
    action: '检查设备网络和照片上传授权，确认上传后的照片可以正常读取，再重新验收。',
  },
  CAMERA_UPLOAD_DIGEST_EMPTY: {
    title: '照片上传检查记录不完整',
    action: '确认照片上传结果已经安全保存并写入本次验收记录，然后重新验收。',
  },
  DEVICE_ENTRY_URL_NOT_STORED: {
    title: '设备扫码入口地址未安全保存',
    action: '重新同步设备扫码入口地址，并确认设备重启后仍能读取该地址。',
  },
  DEVICE_ENTRY_URL_SHA256_MISMATCH: {
    title: '设备扫码入口地址校验失败',
    action: '重新下发当前设备的扫码入口地址，确认设备保存的内容与后台一致后重新验收。',
  },
  ACCEPTANCE_EVIDENCE_MISSING: {
    title: '本次验收尚未收到设备检查结果',
    action: '确认云端验收指令已经到达设备，并检查设备网络和待上传记录；恢复后等待系统继续处理。',
  },
  MACHINE_ACCEPTANCE_FAILED: {
    title: '设备功能检查未通过，但系统没有提供具体原因',
    action: '重新核对本次验收的设备检查结果；若仍没有具体原因，请携带设备序列号和验收次数联系技术支持。',
  },
  ACCEPTANCE_REQUEST_BLOCKED: {
    title: '云端验收指令发送受阻',
    action: '打开对应的后台处理任务，依据最近一次发送记录排除问题后，再恢复任务。',
  },
  ACCEPTANCE_REQUEST_CANCELLED: {
    title: '云端验收指令已取消',
    action: '核对当前袋码登记和本次验收状态；确认仍然有效后，重新核对检查结果以创建新指令。',
  },
  FACTORY_SEAL_AUTHORIZATION_REJECTED: {
    title: '设备拒绝了封存授权',
    action: '核对本次验收采用的检查记录和设备当前状态；修复不一致后，让系统重新发送授权。',
  },
  DEVICE_REJECTED_AUTHORIZATION: {
    title: '设备拒绝了封存授权',
    action: '查看设备返回的原因，核对本次验收和检查记录；不要手工跳过封存校验。',
  },
  ACCEPTANCE_EVIDENCE_NOT_LATEST: {
    title: '封存授权采用的检查记录已经过期',
    action: '先核对后来收到的检查记录和当前验收结果，再按最新一次验收生成封存授权。',
  },
  AUTHORIZATION_FACT_MISSING: {
    title: '封存授权记录缺失',
    action: '检查本次验收对应的封存授权记录和后台处理任务，不要直接在设备侧强制封存。',
  },
  FACTORY_SEAL_AUTHORIZATION_CANCELLED: {
    title: '当前封存授权已取消',
    action: '查看取消原因并核对最新检查结果；不得继续使用已经取消的授权结束出厂模式。',
  },
  FACTORY_SEAL_TASK_BLOCKED: {
    title: '封存授权发送受阻',
    action: '打开对应的后台处理任务，根据最近发送记录恢复云端连接或设备接收条件。',
  },
  FACTORY_SEAL_TASK_CANCELLED: {
    title: '封存授权发送任务已取消',
    action: '核对当前验收采用的检查记录；确认本次验收仍然通过后，重新核对检查结果。',
  },
  FACTORY_SEAL_STATUS_UNKNOWN: {
    title: '后台无法识别当前封存状态',
    action: '停止人工封存操作，记录设备序列号、本次验收次数和发生时间并联系技术支持。',
  },
  DEVICE_OFFLINE: {
    title: '设备当前离线',
    action: '检查物联网卡、蜂窝信号和设备电源；设备恢复在线后，系统会继续发送指令。',
  },
  DEVICE_PRESENCE_UNKNOWN: {
    title: '平台暂时无法确认设备是否在线',
    action: '等待平台取得明确的设备上线信息；若长时间不恢复，请检查设备网络。',
  },
  DEVICE_IDENTITY_UNRESOLVED: {
    title: '物联网平台找不到这台设备',
    action: '核对设备序列号和物联网平台登记信息，修正后重新发送指令。',
  },
  DEVICE_CONFIRMATION_TIMEOUT: {
    title: '指令已发送，但长时间未收到设备确认',
    action: '检查设备网络和日常服务；确认设备恢复后，再从后台处理任务继续。',
  },
  DEVICE_EVIDENCE_TIMEOUT: {
    title: '指令已发送，但未按时收到设备检查结果',
    action: '检查设备日常服务、摄像头、传感器和待上传记录，再从后台处理任务继续。',
  },
  AUTO_RETRY_EXHAUSTED: {
    title: '系统多次发送仍未成功',
    action: '需要人工检查设备网络和物联网平台状态，排除问题后再恢复发送。',
  },
  PERMANENT_TECHNICAL_FAILURE: {
    title: '物联网平台拒绝了本次指令',
    action: '检查设备登记信息、平台权限和指令内容；修正后重新创建或恢复任务。',
  },
  ACCEPTANCE_SNAPSHOT_CHANGED: {
    title: '设备验收结果已经发生变化',
    action: '重新核对最新检查结果，并根据本次验收重新生成封存授权。',
  },
};

export function factoryFailureGuidance(code: string): FactoryFailureGuidance {
  return FAILURE_GUIDANCE[code] ?? {
    title: '设备遇到暂未识别的问题',
    action: '请记录设备序列号和发生时间，并在下方技术诊断中复制报修信息后联系技术支持。',
  };
}

function progressedPastSealIssue(progress: DeviceFactoryProgress): boolean {
  return progress.seal.status === 'ACKNOWLEDGED'
    || progress.seal.status === 'SEALED';
}

function acceptanceRequestDescription(
  progress: DeviceFactoryProgress,
  hasCurrentEvidence: boolean,
  blocked: boolean,
): string {
  if (blocked) return '验收指令发送受阻';
  if (hasCurrentEvidence) return '已收到设备检查结果';
  if (!progress.acceptanceRequest.taskUid) {
    return progress.factoryBags.complete ? '等待系统发起' : '等待袋码完整';
  }
  const attemptResult = progress.acceptanceRequest.latestAttempt?.technicalResult;
  if (attemptResult) return technicalResultLabel(attemptResult);
  if (progress.acceptanceRequest.taskState === 'DONE') {
    return '后台处理已完成，等待设备检查结果';
  }
  return '系统正在安排发送';
}

function sealAuthorizationDescription(
  progress: DeviceFactoryProgress,
  accepted: boolean,
  blocked: boolean,
): string {
  if (accepted) return '设备已接收';
  if (blocked) return '授权发送受阻';
  if (progress.seal.status === 'NOT_ISSUED') return '尚未生成';
  const attemptResult = progress.seal.latestAttempt?.technicalResult;
  if (attemptResult) return technicalResultLabel(attemptResult);
  if (progress.seal.taskUid) {
    return progress.seal.taskState === 'DONE'
      ? '后台处理已完成，等待设备接受'
      : '系统正在安排发送';
  }
  return '封存授权处理中';
}

export function buildFactoryProgressSteps(
  progress: DeviceFactoryProgress,
): FactoryProgressStep[] {
  const hasCurrentEvidence = Boolean(
    progress.acceptance.authoritativeEvidence,
  );
  const acceptanceTaskCreated = Boolean(progress.acceptanceRequest.taskUid)
    || hasCurrentEvidence;
  const acceptanceTaskBlocked = progress.acceptanceRequest.taskState === 'BLOCKED'
    || progress.acceptanceRequest.taskState === 'CANCELLED';
  const sealBlocked = progress.seal.taskState === 'BLOCKED'
    || progress.seal.taskState === 'CANCELLED'
    || progress.seal.status === 'CANCELLED';
  const sealAccepted = progressedPastSealIssue(progress);
  const sealed = progress.seal.status === 'SEALED';
  const flowBlocked = progress.status === 'BLOCKED' && !sealed;
  const localConfirmationBlocked = flowBlocked && sealAccepted;

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
      title: '发起云端验收',
      description: acceptanceRequestDescription(
        progress,
        hasCurrentEvidence,
        acceptanceTaskBlocked,
      ),
      status: acceptanceTaskBlocked
        ? 'error'
        : hasCurrentEvidence
          ? 'finish'
          : acceptanceTaskCreated
            ? 'process'
            : 'wait',
    },
    {
      key: 'acceptance',
      title: '平台验收结果',
      description: progress.acceptance.status === 'PASSED'
        ? '检查通过'
        : progress.acceptance.status === 'FAILED'
          ? '检查未通过'
          : hasCurrentEvidence
            ? '后台正在检查'
            : '等待设备检查结果',
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
      description: sealAuthorizationDescription(
        progress,
        sealAccepted,
        sealBlocked,
      ),
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
        : localConfirmationBlocked
          ? '当前不能继续，请先处理上方问题'
          : progress.seal.status === 'ACKNOWLEDGED'
            ? '等待设备设置页面操作'
            : '尚未开放',
      status: sealed
        ? 'finish'
        : localConfirmationBlocked
          ? 'error'
          : progress.seal.status === 'ACKNOWLEDGED'
            ? 'process'
            : sealBlocked
              ? 'error'
              : 'wait',
    },
    {
      key: 'complete',
      title: '封存完成',
      description: sealed
        ? '已完成'
        : localConfirmationBlocked || sealBlocked
          ? '等待问题处理完成'
          : '等待设备上报',
      status: sealed
        ? 'finish'
        : localConfirmationBlocked || sealBlocked
          ? 'error'
          : 'wait',
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
      description: '平台已收到设备的封存完成通知；现在可以继续为设备分配所属企业和机构。',
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
      description: '请按下方问题说明排除原因；指令已经发出或存在历史记录，都不等于本次验收已经通过。',
    };
  }
  if (progress.seal.status === 'ACKNOWLEDGED') {
    return {
      type: 'warning',
      message: '封存授权已被设备接受',
      description: '等待操作员连接设备设置页面并确认结束出厂模式；此状态还不代表封存完成。',
    };
  }
  if (progress.acceptance.status === 'PASSED') {
    return {
      type: 'info',
      message: '设备功能检查已通过，正在完成封存交接',
      description: '平台已确认本次设备检查通过；请继续等待封存授权送达设备。',
    };
  }
  if (progress.status === 'WAITING_OPERATOR'
    && progress.currentStage === 'FACTORY_BAGS') {
    return {
      type: 'warning',
      message: '等待登记设备的初始回收袋',
      description: '请在厂家小程序扫描设备二维码和每个投口的初始袋码；登记完整后系统会自动继续。',
    };
  }
  return {
    type: 'info',
    message: '出厂接入正在进行',
    description: '系统会自动推进；只有当前步骤明确提示需要处理时，才需要人工操作。',
  };
}

export function factoryIssueAlertType(
  progress: DeviceFactoryProgress,
): 'warning' | 'error' {
  return progress.status === 'BLOCKED' ? 'error' : 'warning';
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
  MACHINE_ACCEPTANCE: '设备功能检查',
  FACTORY_SEAL_AUTHORIZATION: '发送封存授权',
  END_FACTORY_MODE: '在设备设置页面结束出厂模式',
  FACTORY_SEALED: '封存完成',
};

const ACTION_LABELS: Record<string, string> = {
  SCAN_FACTORY_BAGS: '使用厂家小程序补齐初始袋码',
  WAIT_FOR_DEVICE_ONLINE: '等待设备恢复云端连接',
  WAIT_FOR_ACCEPTANCE_REQUEST: '等待系统发起设备功能检查',
  WAIT_FOR_ACCEPTANCE_EVIDENCE: '等待设备上传检查结果',
  VIEW_ACCEPTANCE_FAILURES: '查看设备功能检查未通过原因',
  REEVALUATE_ACCEPTANCE: '排除故障后重新核对检查结果',
  WAIT_FOR_FACTORY_SEAL_AUTHORIZATION: '等待系统发送封存授权',
  WAIT_FOR_FACTORY_SEAL_ACKNOWLEDGEMENT: '等待设备接受封存授权',
  CONFIRM_END_FACTORY_MODE: '连接设备设置页面，确认结束出厂模式',
  OPEN_RELIABLE_TASK: '打开后台处理任务查看发送记录',
  RESOLVE_ACCEPTANCE_TASK_BLOCKER: '排除设备功能检查指令的发送问题',
  RESOLVE_FACTORY_SEAL_TASK_BLOCKER: '排除封存授权的发送问题',
  VIEW_FACTORY_SEAL_CANCELLATION: '查看封存授权取消原因',
  RESTORE_DEVICE_ASSET: '先恢复设备资产为正常状态',
  CONTACT_SUPPORT: '记录诊断信息并联系技术支持',
};

export function factoryStageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? '接入状态待确认';
}

export function factoryActionLabel(action: string): string {
  return ACTION_LABELS[action] ?? '请查看处理建议或联系技术支持';
}

export function taskStateLabel(state: string | null): string {
  if (!state) return '尚未创建';
  return ({
    PENDING: '系统处理中',
    RUNNING: '后台正在处理',
    DONE: '后台处理已完成',
    CANCELLED: '已取消',
    BLOCKED: '已阻断',
  } as Record<string, string>)[state] ?? '状态待确认';
}

export function technicalResultLabel(result: string | null): string {
  if (!result) return '尚无发送结果';
  return ({
    TECHNICAL_SUCCESS: '物联网平台已受理，等待设备确认',
    NO_ACTION_REQUIRED: '无需再次发送',
    RETRYABLE_FAILURE: '发送暂未成功，系统将自动重试',
    OUTCOME_UNKNOWN: '发送结果暂时无法确认，系统正在核对',
    PERMANENT_TECHNICAL_FAILURE: '发送未成功，需要人工处理',
    TARGET_OFFLINE: '设备离线，恢复连接后系统会继续',
    TARGET_NOT_FOUND: '物联网平台暂时找不到这台设备',
  } as Record<string, string>)[result] ?? '发送结果待确认';
}

export function sealStatusLabel(status: DeviceFactoryProgress['seal']['status']): string {
  return {
    NOT_ISSUED: '尚未生成',
    PENDING: '封存授权处理中',
    ACKNOWLEDGED: '设备已接受，等待设置页面结束出厂',
    CANCELLED: '授权已取消',
    SEALED: '封存完成',
  }[status];
}

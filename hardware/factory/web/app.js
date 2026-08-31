"use strict";

const byId = (id) => document.getElementById(id);
let currentStatus = null;
let currentFlowNode = null;
let requestRunning = false;
let refreshRunning = false;
let pollTimer = null;
let portalTerminal = false;
let pendingSealUid = null;
let lastProgressAnnouncement = null;

const REQUEST_TIMEOUT_MS = Object.freeze({
  status: 10000,
  action: 85000,
  seal: 12000,
});

const NODE_META = Object.freeze({
  BOOT_AND_PORTAL: { number: "01", title: "启动准备与热点", instruction: "等待系统准备、磁盘扩容和出厂热点就绪。" },
  LOCAL_HARDWARE_ACCEPTANCE: { number: "02", title: "P7 本地硬件验收", instruction: "按页面按钮完成当前设备的本地硬件检查。" },
  CELLULAR_AND_TIME: { number: "03", title: "蜂窝联网与校时", instruction: "设备正在通过 Air780E 建立蜂窝网络并校准系统时间。" },
  ENROLLMENT_AND_CREDENTIALS: { number: "04", title: "自注册与正式凭证", instruction: "设备正在用出厂资格注册，并安全保存正式凭证。" },
  RUNTIME_AND_MQTT: { number: "05", title: "正式服务与 OneNet", instruction: "设备正在交接串口、启动正式硬件服务并连接 OneNet。" },
  FACTORY_BAGS: { number: "06", title: "厂家扫码与初始袋码", instruction: "请在厂家小程序扫描设备二维码，再逐一扫描每个投口的初始袋码。" },
  CLOUD_EVIDENCE: { number: "07", title: "P8 证据采集与上传", instruction: "设备正在采集运行证据、拍照、上传 COS，并可靠回报后端。" },
  CLOUD_DECISION_AND_AUTHORIZATION: { number: "08", title: "云端判定与封存授权", instruction: "等待后端判定当前代次机器验收，并向设备下发封存授权。" },
  FACTORY_SEAL: { number: "09", title: "结束出厂模式", instruction: "云端验收通过后，由操作员确认永久结束出厂模式。" },
});

const STATE_META = Object.freeze({
  PENDING: { label: "等待", className: "is-pending" },
  ACTIVE: { label: "进行中", className: "is-active" },
  WAITING_OPERATOR: { label: "待操作", className: "is-waiting" },
  BLOCKED: { label: "需处理", className: "is-blocked" },
  COMPLETED: { label: "已完成", className: "is-completed" },
  UNKNOWN: { label: "状态未知", className: "is-unknown" },
});

const STEP_LABELS = Object.freeze({
  SYSTEM_PREPARED: "系统与磁盘准备",
  FACTORY_PORTAL_READY: "出厂热点与网页",
  MCU: "MCU 通信和自检",
  WEIGHT: "称重检查",
  MCU_UPDATE_LINE: "升级线路",
  CAMERAS: "双摄检查",
  DELIVERY: "投递动作",
  CLEAN: "清运动作",
  REPORT: "P7 验收报告",
  AIR780E_PROFILE: "Air780E 网络配置",
  CELLULAR_UPLINK: "蜂窝链路",
  TRUSTED_TIME: "系统时间",
  IDENTITY_PREPARED: "设备身份准备",
  CHALLENGE_ACQUIRED: "取得注册挑战",
  REQUEST_SUBMITTED: "提交注册请求",
  CREDENTIALS_INSTALLED: "正式凭证落盘",
  K1_REMOVED: "清理 K1",
  UART_HANDOFF: "串口安全交接",
  HARDWARE_SERVICE: "正式硬件服务",
  UART_READY: "MCU 串口就绪",
  MQTT_CONNECTED: "OneNet MQTT 连接",
  DEVICE_ENTRY_URL: "设备入口地址",
  P8_REQUEST: "收到 P8 请求",
  STORE_CHECK: "持久化存储",
  CONFIG_CHECK: "配置持久化",
  MCU_AND_SENSORS: "MCU 与传感器",
  CAMERA_CAPTURE: "双摄拍照",
  COS_UPLOAD_READBACK: "COS 上传与读回",
  EVIDENCE_RECORDED: "证据可靠落盘",
  ONENET_TRANSPORT_ACCEPTED: "OneNet 已接收上报",
  PLATFORM_CONFIRMED: "后端确认接收",
  CURRENT_GENERATION_AUTHORIZED: "当前代次封存授权",
  SEAL_AUTHORIZATION: "封存授权",
  LOCAL_CONFIRMATION_AVAILABLE: "允许人工确认",
  SEAL_MARKER_SAVED: "封存事实落盘",
});

const DETAIL_DESCRIPTIONS = Object.freeze({
  BOOT_READY: "系统准备和出厂热点均已就绪。",
  NOT_RUN: "等待操作员开始本地硬件验收。",
  COMPLETE: "本地硬件验收已经通过。",
  CELLULAR_AND_TIME_READY: "蜂窝网络可用，系统时间已经校准。",
  TIME_SYNC_PENDING: "蜂窝网络已建立，正在等待系统时间达到可信状态。",
  ENROLLMENT_COMPLETE: "正式设备凭证已经保存，K1 和一次性注册材料已清理。",
  IDENTITY_PREPARATION: "正在准备设备身份和注册材料。",
  CHALLENGE_REQUEST: "正在向后端申请一次性注册挑战。",
  ENROLLMENT_SUBMISSION: "正在提交设备注册请求。",
  CREDENTIAL_INSTALLATION: "后端已接受注册，正在验证并保存正式凭证。",
  K1_CLEANUP: "正式凭证已落盘，正在删除 K1 和一次性注册材料。",
  RETRY_WAIT: "本次注册未成功，系统会自动重试。",
  RUNTIME_READY: "串口已经安全交接，正式硬件服务和 OneNet 均在线。",
  RUNTIME_STATUS_STALE: "正式硬件服务的状态超过 15 秒没有更新。",
  WAITING_ONENET_ACCEPTANCE: "证据已可靠落盘，正在等待 OneNet 接收上报。",
  WAITING_BACKEND_CONFIRMATION: "OneNet 已接收上报，正在等待后端确认接收。",
  SCAN_DEVICE_AND_FACTORY_BAGS: "请切换到厂家小程序，扫描设备和每个投口的初始袋码。",
  P8_REQUEST_RECEIVED: "初始袋码条件已满足，后端已经发起 P8 云端机器验收。",
  REQUEST_RECEIVED: "设备已经收到 P8 请求，正在开始证据采集。",
  PERSISTENT_STORE_CHECK: "正在检查本地可靠存储。",
  CONFIGURATION_CHECK: "正在核对当前设备配置。",
  MCU_SENSOR_CHECK: "正在采集 MCU 和传感器证据。",
  CAMERA_CAPTURE: "正在使用双摄拍摄验收照片。",
  COS_UPLOAD_READBACK: "正在上传照片并从 COS 读回校验。",
  EVIDENCE_PERSISTENCE: "正在将本次证据可靠写入本地存储。",
  EVIDENCE_RECORDED: "证据已经落盘，正在等待可靠送达后端。",
  EVIDENCE_CONFIRMED: "P8 证据已可靠送达并由后端确认接收。",
  WAITING_CLOUD_DECISION: "证据已送达，正在等待后端判定和封存授权。若长时间没有推进，请查看后台设备详情。",
  CLOUD_ACCEPTANCE_PASSED: "当前验收代次已由后端判定 PASSED，封存授权已经到达设备。",
  SEAL_READY: "所有封存前置条件已满足，请确认结束出厂模式。",
  SEALED_RESPONSE_PENDING: "封存事实已经可靠保存，正在把最终结果交给本页面。",
  SEALED_CLEANUP_PENDING: "封存事实已经保存，正在关闭热点和出厂服务。",
  SEALED: "设备已永久结束出厂模式。",
  STATUS_UNAVAILABLE: "暂时无法取得这一阶段的状态。",
  FACTORY_SEAL_NOT_AVAILABLE: "暂时无法读取封存授权状态。",
});

const ERROR_DESCRIPTIONS = Object.freeze({
  NONE: "当前没有错误",
  STATUS_UNAVAILABLE: "状态来源暂时不可用",
  TIME_SYNC_PENDING: "正在等待网络时间可信",
  TIME_TRUST_QUERY_FAILED: "无法读取系统时间同步状态",
  CHRONY_ONLINE_FAILED: "无法启用网络时间源",
  CHRONY_ACTIVITY_FAILED: "无法读取网络时间源状态",
  CHRONY_SOURCES_UNAVAILABLE: "没有可用的网络时间源",
  CHRONY_REFRESH_FAILED: "刷新网络时间源失败",
  CHRONY_BURST_FAILED: "发起快速校时失败",
  CHRONY_WAITSYNC_FAILED: "等待校时结果失败",
  TIME_SYNC_INTERNAL_ERROR: "校时服务内部错误",
  CELLULAR_DEVICE_NOT_FOUND: "没有检测到 Air780E 蜂窝网卡",
  CELLULAR_ACTIVATION_FAILED: "Air780E 网络连接未能激活",
  ACTIVE_SWAP_DETECTED: "检测到设备身份材料被替换",
  BACKEND_URL_MISSING: "设备注册后端地址未配置",
  CHALLENGE_EXPIRED: "设备注册挑战已经过期",
  CHALLENGE_RESPONSE_INVALID: "设备注册挑战响应格式无效",
  CREDENTIAL_INSTALL_FAILED: "正式设备凭证保存失败",
  ENROLLMENT_NETWORK_UNAVAILABLE: "设备注册时无法连接后端",
  ENROLLMENT_BACKEND_TEMPORARY: "后端暂时无法完成设备注册",
  ENROLLMENT_BACKEND_REJECTED: "后端拒绝了设备注册",
  ENROLLMENT_INTERNAL_ERROR: "设备注册服务内部错误",
  ENROLLMENT_KEY_INVALID: "设备注册密钥无效",
  ENROLLMENT_PENDING: "设备注册尚未完成",
  ENROLLMENT_RESPONSE_INVALID: "后端注册响应格式无效",
  ENROLLMENT_STATE_INVALID: "本地注册状态无效",
  ENROLLMENT_STATE_PERMISSIONS_INVALID: "本地注册状态文件权限不安全",
  K1_CLEANUP_FAILED: "正式凭证已保存，但清理 K1 失败",
  RUNTIME_STATUS_STALE: "正式硬件服务状态已经过期",
  RUNTIME_BOOT_FAILED: "正式硬件服务启动失败",
  RUNTIME_SERVICE_STOPPED: "正式硬件服务已经停止",
  MQTT_CONNECT_FAILED: "OneNet MQTT 连接失败",
  MQTT_DISCONNECTED: "OneNet MQTT 连接已经断开",
  MQTT_FAILED: "OneNet MQTT 通信失败",
  UART_HANDSHAKE_FAILED: "MCU 串口握手失败",
  UART_OPEN_FAILED: "无法打开 MCU 串口",
  UART_QUERY_STATE_FAILED: "无法读取 MCU 状态",
  UART_RECOVERY_FAILED: "MCU 串口恢复失败",
  UART_FAILED: "MCU 串口通信失败",
  UART_DISCONNECTED: "MCU 串口连接已经断开",
  P8_STORAGE_CHECK_FAILED: "P8 本地存储检查失败",
  P8_CONFIGURATION_CHECK_FAILED: "P8 设备配置检查失败",
  P8_MCU_SENSOR_CHECK_FAILED: "P8 MCU 或传感器检查失败",
  P8_CAMERA_CAPTURE_FAILED: "P8 摄像头拍照失败",
  P8_COS_UPLOAD_READBACK_FAILED: "P8 照片上传或读回校验失败",
  P8_EVIDENCE_PERSISTENCE_FAILED: "P8 证据可靠落盘失败",
  P8_DEVICE_ENTRY_URL_FAILED: "P8 设备入口地址检查失败",
  P8_GRANT_NOT_AVAILABLE: "P8 尚未取得照片上传授权",
  P8_EXECUTION_FAILED: "P8 执行失败",
  ACCEPTANCE_EVIDENCE_DELIVERY_DEAD: "P8 验收证据多次发送失败",
});

const ACTIONS = Object.freeze({
  START: {
    label: "开始本地硬件验收",
    prompt: "确认设备周围无人、普通硬件服务未运行，并开始本机离线验收？",
    parameters: () => ({ confirmOfflineAcceptance: true, mcuUpdateLineInstalled: selectedUpdateLineState() }),
  },
  RESTART_FAILED_RUN: {
    label: "重新开始本地验收",
    prompt: "确认故障已经排除，并重新开始本地硬件验收？",
    parameters: () => ({ confirmRestartFailedAcceptance: true, mcuUpdateLineInstalled: selectedUpdateLineState() }),
  },
  CHECK_MCU: { label: "检查 MCU 与传感器", prompt: "确认设备当前没有进行投递或清运动作？", parameters: () => ({}) },
  CAPTURE_EMPTY_WEIGHT: { label: "采集稳定空载重量", prompt: "请清空承重面。确认当前没有砝码或测试物？", parameters: () => ({ confirmScaleEmpty: true }) },
  CAPTURE_LOADED_WEIGHT: { label: "采集 500 g 重量", prompt: "请将 500 g 砝码放稳。确认已正确放置？", parameters: () => ({ confirm500gPlaced: true }) },
  CONFIRM_WEIGHT_REMOVED: { label: "确认砝码已取下", prompt: "请取下 500 g 砝码。确认承重面已经恢复空载？", parameters: () => ({ confirm500gRemoved: true }) },
  CAPTURE_CAMERAS: { label: "拍摄双摄确认图", prompt: "将立即使用两台摄像头拍摄临时画面。确认继续？", parameters: () => ({ confirmCaptureNow: true }) },
  CONFIRM_CAMERAS: {
    label: "确认摄像头角色",
    prompt: "确认上方箱外、箱内画面与实际安装位置完全一致？",
    parameters: () => ({ reviewNonce: currentStatus.factoryTest.cameraReview.nonce, outsideRoleConfirmed: true, insideRoleConfirmed: true }),
  },
  CHECK_UPGRADE_LINE: { label: "检查 MCU 升级线路", prompt: "将只读识别 STM32 ROM，不会擦除或写入 Flash。确认设备周围安全？", parameters: () => ({ confirmReadOnlyBootloaderProbe: true }) },
  RUN_DELIVERY: { label: "执行投递硬件测试", prompt: "将真实驱动投递机构。确认周围无人、机构无阻挡？", parameters: () => ({ operatorAreaSafeConfirmed: true }) },
  CONFIRM_DELIVERY_AREA_SAFE: { label: "确认投递区域安全", prompt: "请现场检查机构已停止、周围无人且没有阻挡物。确认安全？", parameters: () => ({ operatorAreaSafeConfirmed: true }) },
  RUN_CLEAN: { label: "执行清运硬件测试", prompt: "将真实驱动清运锁。确认周围无人、机构无阻挡？", parameters: () => ({ operatorAreaSafeConfirmed: true }) },
  CONFIRM_CLEAN_DOOR: { label: "确认清运门已关闭", prompt: "请现场观察并确认清运门已经完全关闭。", parameters: () => ({ cleanDoorClosedConfirmed: true }) },
  RECOVER: {
    label: "执行受控恢复",
    prompt: "将停止继续下发、复位 MCU 并重新验证。确认执行？",
    parameters: () => ({ confirmRecovery: true, cleanDoorClosedConfirmed: currentStatus.factoryTest.recovery?.context === "CLEAN" }),
  },
  FINALIZE: {
    label: "生成 P7 验收报告",
    prompt: "确认所有本地硬件项目均已完成，并生成验收报告？",
    parameters: () => ({
      confirmFinalize: true,
      ...(currentStatus.factoryTest.mcuPeripheralEvidenceMode === "SIMULATED_PERIPHERALS" ? { confirmSimulatedPeripheralEvidence: true } : {}),
    }),
  },
});

const ACTION_INSTRUCTIONS = Object.freeze({
  START: "选择 MCU 升级线路的实际装配情况，然后开始本地验收。",
  RESTART_FAILED_RUN: "排除上次错误后，重新建立一轮独立验收。",
  CHECK_MCU: "系统将查询 MCU 固件身份和传感器自检结果。",
  CAPTURE_EMPTY_WEIGHT: "清空承重面，等待系统取得稳定空载值。",
  CAPTURE_LOADED_WEIGHT: "放置 500 g 砝码并保持稳定。",
  CONFIRM_WEIGHT_REMOVED: "取下砝码，确认称重恢复到空载范围。",
  CAPTURE_CAMERAS: "拍摄本次临时画面，用于确认箱外和箱内摄像头。",
  CONFIRM_CAMERAS: "查看两张临时画面，确认摄像头角色正确。",
  CHECK_UPGRADE_LINE: "只读检查升级线路，完成后 MCU 会返回原应用。",
  RUN_DELIVERY: "操作会真实驱动机构；开始前确认人员和障碍物已经离开。",
  CONFIRM_DELIVERY_AREA_SAFE: "动作结束后重新检查现场，再确认区域安全。",
  RUN_CLEAN: "操作会真实驱动清运锁；开始前确认现场安全。",
  CONFIRM_CLEAN_DOOR: "动作结束后现场确认清运门已经完全关闭。",
  RECOVER: "恢复不会重发投递或清运命令，只复位并重新验证 MCU。",
  FINALIZE: "保存本轮 P7 本地验收报告，随后自动进入联网注册阶段。",
});

function selectedUpdateLineState() {
  const value = byId("mcu-update-line-installed").value;
  if (value === "true") return true;
  if (value === "false") return false;
  return null;
}

function createUuidV4() {
  if (!window.crypto || typeof window.crypto.getRandomValues !== "function") {
    throw new Error("BROWSER_RANDOM_UNAVAILABLE");
  }
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return [hex.slice(0, 4), hex.slice(4, 6), hex.slice(6, 8), hex.slice(8, 10), hex.slice(10)]
    .map((group) => group.join(""))
    .join("-");
}

async function fetchWithTimeout(resource, options, timeoutMs) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(resource, { ...options, signal: controller.signal });
    const body = await response.json();
    return { response, body };
  } catch (error) {
    if (controller.signal.aborted) throw new Error("REQUEST_TIMEOUT");
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

function setTextIfChanged(element, value) {
  if (element.textContent !== value) element.textContent = value;
}

function stateMeta(state) {
  return STATE_META[state] || STATE_META.UNKNOWN;
}

function describeDetail(node) {
  if (DETAIL_DESCRIPTIONS[node.detailCode]) return DETAIL_DESCRIPTIONS[node.detailCode];
  const meta = NODE_META[node.id];
  if (node.state === "COMPLETED") return `${meta?.title || "当前阶段"}已完成。`;
  return meta?.instruction || "请查看诊断信息并等待状态更新。";
}

function describeError(node) {
  const code = node.errorCode && node.errorCode !== "NONE" ? node.errorCode : node.detailCode;
  return { code, description: ERROR_DESCRIPTIONS[code] || "设备报告了未归类的问题" };
}

function recoveryInstruction(node, code) {
  if (node.id === "CELLULAR_AND_TIME") return "检查 Air780E 的 USB/RNDIS 连接、物联网卡状态和蜂窝信号，排除后设备会自动重试。";
  if (node.id === "ENROLLMENT_AND_CREDENTIALS") return "先确认蜂窝网络和时间正常；记录错误码后在后台或远程日志中检查注册请求。";
  if (node.id === "RUNTIME_AND_MQTT") return "检查正式硬件服务、UART 接线和 MQTT 连接；状态过期时先确认服务是否仍在运行。";
  if (node.id === "CLOUD_EVIDENCE") return "检查摄像头、COS 上传和本地可靠发件箱；记录错误码后在后台设备详情继续定位。";
  if (node.id === "CLOUD_DECISION_AND_AUTHORIZATION") return "热点页不会接收云端 FAILED 原因，请到后台设备详情查看权威判定和处理建议。";
  if (node.id === "FACTORY_SEAL") return "不要绕过封存门禁；按错误码修复运行服务或授权状态后等待页面自动恢复。";
  if (code === "STATUS_UNAVAILABLE") return "保持热点连接并刷新；若持续无状态，请检查对应设备服务。";
  return "记录下方错误码，排除对应硬件或服务问题后再继续。";
}

function renderFlow(flow) {
  const nodes = Array.isArray(flow?.nodes) ? flow.nodes : [];
  const currentId = typeof flow?.currentNode === "string" ? flow.currentNode : "BOOT_AND_PORTAL";
  currentFlowNode = nodes.find((node) => node.id === currentId) || {
    id: currentId,
    state: "UNKNOWN",
    detailCode: "STATUS_UNAVAILABLE",
    errorCode: "NONE",
    steps: [],
  };

  for (const item of document.querySelectorAll("#progress-chain [data-node]")) {
    const node = nodes.find((candidate) => candidate.id === item.dataset.node) || { state: "PENDING" };
    const meta = stateMeta(node.state);
    item.className = `${meta.className}${item.dataset.node === currentId ? " is-current" : ""}`;
    if (node.id === currentId) {
      item.setAttribute("aria-current", "step");
    } else {
      item.removeAttribute("aria-current");
    }
    const state = item.querySelector('[data-role="node-state"]');
    if (state) state.textContent = meta.label;
  }

  const nodeMeta = NODE_META[currentFlowNode.id] || { number: "--", title: "状态未知" };
  const currentStateMeta = stateMeta(currentFlowNode.state);
  setTextIfChanged(byId("current-index"), `当前节点 ${nodeMeta.number}`);
  setTextIfChanged(byId("current-title"), nodeMeta.title);
  setTextIfChanged(byId("current-state"), currentStateMeta.label);
  byId("current-state").className = `state-badge ${currentStateMeta.className}`;
  const currentDescription = describeDetail(currentFlowNode);
  setTextIfChanged(byId("current-detail"), currentDescription);

  const issueVisible = ["BLOCKED", "UNKNOWN"].includes(currentFlowNode.state)
    || (currentFlowNode.errorCode && currentFlowNode.errorCode !== "NONE");
  byId("issue-panel").hidden = !issueVisible;
  let problem = null;
  if (issueVisible) {
    problem = describeError(currentFlowNode);
    setTextIfChanged(byId("issue-title"), problem.description);
    setTextIfChanged(
      byId("issue-action"),
      recoveryInstruction(currentFlowNode, problem.code),
    );
    setTextIfChanged(byId("issue-code"), problem.code);
    byId("diagnostics").open = true;
  }

  const announcementSignature = [
    currentFlowNode.id,
    currentFlowNode.state,
    currentFlowNode.detailCode,
    currentFlowNode.errorCode,
  ].join("|");
  if (announcementSignature !== lastProgressAnnouncement) {
    lastProgressAnnouncement = announcementSignature;
    const issueAnnouncement = problem
      ? `问题：${problem.description}，错误码 ${problem.code}。`
      : "";
    setTextIfChanged(
      byId("progress-announcement"),
      `${nodeMeta.title}，${currentStateMeta.label}。${currentDescription}${issueAnnouncement}`,
    );
  }

  const substeps = byId("current-substeps");
  substeps.replaceChildren();
  for (const step of Array.isArray(currentFlowNode.steps) ? currentFlowNode.steps : []) {
    const item = document.createElement("li");
    item.className = stateMeta(step.state).className;
    item.textContent = `${STEP_LABELS[step.id] || step.id} · ${stateMeta(step.state).label}`;
    substeps.appendChild(item);
  }

  if (currentId !== renderFlow.lastCurrentId) {
    renderFlow.lastCurrentId = currentId;
    const currentElement = document.querySelector(`#progress-chain [data-node="${currentId}"]`);
    if (currentElement) {
      window.requestAnimationFrame(() => currentElement.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
        block: "nearest",
        inline: "center",
      }));
    }
  }
}

function updateOperatorPanel(status) {
  const factory = status.factoryTest || {};
  const allowed = Array.isArray(factory.allowedActions) ? factory.allowedActions : [];
  const action = allowed[0];
  const definition = ACTIONS[action];
  const button = byId("primary-action");
  button.dataset.operation = action || "";
  button.textContent = definition?.label || "暂无可执行操作";
  button.hidden = !definition;
  button.disabled = requestRunning || !definition || !factory.executorAvailable;
  byId("action-instruction").textContent = definition
    ? ACTION_INSTRUCTIONS[action]
    : describeDetail(currentFlowNode || { id: "BOOT_AND_PORTAL", state: "UNKNOWN", detailCode: "STATUS_UNAVAILABLE" });
  byId("executor-state").textContent = definition
    ? factory.phase || "待操作"
    : currentFlowNode?.state === "WAITING_OPERATOR" ? "等待人工" : "自动推进";

  const selectingUpdateLine = action === "START" || action === "RESTART_FAILED_RUN";
  byId("update-line-selection").hidden = !selectingUpdateLine;
  byId("mcu-update-line-installed").disabled = requestRunning || !selectingUpdateLine;
  byId("simulation-warning").hidden = !(
    factory.mcuPeripheralEvidenceMode === "SIMULATED_PERIPHERALS"
    && currentFlowNode?.id === "LOCAL_HARDWARE_ACCEPTANCE"
  );

  const recovery = factory.recovery;
  byId("recovery-panel").hidden = !recovery;
  if (recovery) {
    byId("recovery-reason").textContent = `${recovery.context || "UNKNOWN"} · ${recovery.resultCode || "RECOVERY_REQUIRED"}`;
  }

  const review = factory.cameraReview;
  byId("camera-review").hidden = !review;
  if (review) {
    byId("camera-outside").src = review.outsideImage;
    byId("camera-inside").src = review.insideImage;
  } else {
    byId("camera-outside").removeAttribute("src");
    byId("camera-inside").removeAttribute("src");
  }

  const seal = status.factorySeal || {};
  const sealVisible = factory.status === "PASSED" && seal.confirmAllowed === true;
  byId("seal-panel").hidden = !sealVisible;
  byId("seal-confirm").disabled = requestRunning || !sealVisible;
}

function updateStatus(status) {
  currentStatus = status;
  renderFlow(status.factoryFlow);
  updateOperatorPanel(status);
  byId("stage").textContent = status.stage || "UNKNOWN";
  byId("release").textContent = status.image?.releaseId || "UNKNOWN";
  byId("test-status").textContent = status.factoryTest?.status || "NOT_RUN";
  byId("time-trusted").textContent = status.system?.timeTrusted === true ? "已同步" : "未同步";
  byId("seal-status").textContent = status.factorySeal?.statusCode || "UNKNOWN";
  byId("last-error").textContent = status.lastErrorCode || "NONE";
  byId("link-lamp").className = "connection__lamp is-online";
  byId("link-state").textContent = "设备网页在线";
  byId("last-update").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const sealCode = status.factorySeal?.statusCode;
  if (["SEALED_RESPONSE_PENDING", "SEALED_CLEANUP_PENDING", "SEALED"].includes(sealCode)) {
    showSealTerminal();
    if (sealCode === "SEALED_RESPONSE_PENDING" && pendingSealUid) {
      void nextPaint().then(() => acknowledgeSealPresented(pendingSealUid));
    }
  }
}

function schedulePoll() {
  window.clearTimeout(pollTimer);
  if (portalTerminal || document.hidden) return;
  pollTimer = window.setTimeout(() => void refreshStatus(), 3000);
}

async function refreshStatus() {
  if (portalTerminal || refreshRunning || requestRunning) {
    schedulePoll();
    return;
  }
  refreshRunning = true;
  byId("refresh").disabled = true;
  try {
    const { response, body } = await fetchWithTimeout("/api/v1/status", {
      method: "GET",
      cache: "no-store",
      credentials: "omit",
      headers: { Accept: "application/json" },
    }, REQUEST_TIMEOUT_MS.status);
    if (!response.ok) throw new Error(`HTTP_${response.status}`);
    updateStatus(body);
  } catch (error) {
    byId("link-lamp").className = "connection__lamp is-error";
    byId("link-state").textContent = error.message === "REQUEST_TIMEOUT"
      ? "设备响应超时"
      : "状态读取失败";
    byId("last-update").textContent = error.message === "REQUEST_TIMEOUT"
      ? "请求已停止，系统会自动重试"
      : "请确认手机仍连接出厂热点";
  } finally {
    refreshRunning = false;
    byId("refresh").disabled = false;
    schedulePoll();
  }
}

async function postAction(operation, parameters) {
  if (!currentStatus || requestRunning) return;
  requestRunning = true;
  updateOperatorPanel(currentStatus);
  byId("action-result").textContent = "正在执行，请勿重复点击或断电……";
  try {
    const { response, body: result } = await fetchWithTimeout("/api/v1/acceptance/action", {
      method: "POST",
      cache: "no-store",
      credentials: "omit",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-EcoBin-Factory-Action": "1",
      },
      body: JSON.stringify({ operation, expectedRevision: currentStatus.factoryTest.revision, parameters }),
    }, REQUEST_TIMEOUT_MS.action);
    if (!response.ok) throw new Error(result.error || `HTTP_${response.status}`);
    byId("action-result").textContent = result.idempotent ? "该操作已经执行，未重复驱动硬件。" : "操作结果已保存。";
  } catch (error) {
    const message = error.message === "REQUEST_TIMEOUT"
      ? "请求等待超时，正在核实设备状态"
      : error.message || "UNKNOWN";
    byId("action-result").textContent = `操作未完成：${message}`;
  } finally {
    requestRunning = false;
    await refreshStatus();
  }
}

async function performPrimaryAction() {
  const operation = byId("primary-action").dataset.operation;
  const definition = ACTIONS[operation];
  if (!definition || requestRunning) return;
  if (["START", "RESTART_FAILED_RUN"].includes(operation) && selectedUpdateLineState() === null) {
    byId("action-result").textContent = "请先选择 MCU 升级线路的实际装配情况。";
    return;
  }
  let prompt = definition.prompt;
  if (operation === "FINALIZE" && currentStatus.factoryTest.mcuPeripheralEvidenceMode === "SIMULATED_PERIPHERALS") {
    prompt = "当前报告会记录测试证据来源。确认生成 P7 验收报告并继续后续接入流程？";
  }
  if (!window.confirm(prompt)) return;
  await postAction(operation, definition.parameters());
}

function nextPaint() {
  return new Promise((resolve) => {
    window.requestAnimationFrame(() => window.requestAnimationFrame(resolve));
  });
}

async function acknowledgeSealPresented(uid) {
  try {
    await fetch("/api/v1/acceptance/action", {
      method: "POST",
      cache: "no-store",
      credentials: "omit",
      keepalive: true,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-EcoBin-Factory-Action": "1",
      },
      body: JSON.stringify({
        operation: "ACK_FACTORY_SEAL_PRESENTED",
        parameters: { operatorConfirmationUid: uid },
      }),
    });
  } catch (_error) {
    // The root controller has a five-second fail-closed timeout.  Losing this
    // best-effort acknowledgement must never turn a durable seal into failure.
  }
}

function showSealTerminal() {
  portalTerminal = true;
  window.clearTimeout(pollTimer);
  document.querySelector(".topbar").inert = true;
  byId("factory-workspace").inert = true;
  document.body.classList.add("is-terminal");
  const terminal = byId("seal-terminal");
  terminal.hidden = false;
  terminal.focus();
}

async function confirmSeal() {
  if (!currentStatus || requestRunning || portalTerminal) return;
  if (!window.confirm("这是单向操作：成功后出厂热点不会再次开放。确认结束出厂模式？")) return;
  requestRunning = true;
  byId("seal-confirm").disabled = true;
  byId("seal-result").textContent = "正在核对当前代次授权并保存封存事实……";
  try {
    pendingSealUid ||= createUuidV4();
    const { response, body: result } = await fetchWithTimeout("/api/v1/acceptance/action", {
      method: "POST",
      cache: "no-store",
      credentials: "omit",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-EcoBin-Factory-Action": "1",
      },
      body: JSON.stringify({
        operation: "CONFIRM_FACTORY_SEAL",
        expectedRevision: currentStatus.factoryTest.revision,
        parameters: { operatorConfirmationUid: pendingSealUid },
      }),
    }, REQUEST_TIMEOUT_MS.seal);
    if (!response.ok) throw new Error(result.error || `HTTP_${response.status}`);
    requestRunning = false;
    showSealTerminal();
    await nextPaint();
    void acknowledgeSealPresented(pendingSealUid);
  } catch (error) {
    const message = error.message === "BROWSER_RANDOM_UNAVAILABLE"
      ? "当前浏览器无法生成安全请求标识，请更换浏览器"
      : error.message === "REQUEST_TIMEOUT"
        ? "确认请求等待超时，正在核实设备状态"
        : error.message || "UNKNOWN";
    byId("seal-result").textContent = `没有收到完成响应：${message}。若热点仍在线，可使用同一请求重试；若热点关闭，说明设备已进入封存清理。`;
    requestRunning = false;
    byId("seal-confirm").disabled = false;
    void refreshStatus();
  }
}

byId("refresh").addEventListener("click", () => void refreshStatus());
byId("primary-action").addEventListener("click", () => void performPrimaryAction());
byId("seal-confirm").addEventListener("click", () => void confirmSeal());
byId("seal-terminal").addEventListener("keydown", (event) => {
  if (event.key !== "Tab") return;
  event.preventDefault();
  byId("seal-terminal").focus();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    window.clearTimeout(pollTimer);
  } else {
    void refreshStatus();
  }
});

void refreshStatus();

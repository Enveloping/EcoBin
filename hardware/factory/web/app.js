"use strict";

const byId = (id) => document.getElementById(id);
let currentStatus = null;
let requestRunning = false;

const ACTIONS = {
  START: {
    label: "开始离线硬件验收",
    prompt: "确认设备周围无人、普通硬件服务未运行，并开始本机离线验收？",
    parameters: () => ({ confirmOfflineAcceptance: true }),
  },
  RESTART_FAILED_RUN: {
    label: "重新开始失败的验收",
    prompt: "旧报告将保留为历史文件内容直到新验收完成。确认重新开始？",
    parameters: () => ({ confirmRestartFailedAcceptance: true }),
  },
  CHECK_MCU: {
    label: "检查 MCU revision 2 与 F1",
    prompt: "确认设备当前没有进行投递或清运动作？",
    parameters: () => ({}),
  },
  CAPTURE_EMPTY_WEIGHT: {
    label: "采集稳定空载重量",
    prompt: "请清空承重面。确认当前没有砝码或测试物？",
    parameters: () => ({ confirmScaleEmpty: true }),
  },
  CAPTURE_LOADED_WEIGHT: {
    label: "采集 500 g 稳定重量",
    prompt: "请将 500 g 砝码放稳。确认已放置且没有接触箱体其他位置？",
    parameters: () => ({ confirm500gPlaced: true }),
  },
  CONFIRM_WEIGHT_REMOVED: {
    label: "确认砝码已取下并复测",
    prompt: "请取下 500 g 砝码。确认承重面已恢复空载？",
    parameters: () => ({ confirm500gRemoved: true }),
  },
  CAPTURE_CAMERAS: {
    label: "拍摄本次双摄确认图",
    prompt: "将立即使用两台真实摄像头拍摄临时画面。确认继续？",
    parameters: () => ({ confirmCaptureNow: true }),
  },
  CONFIRM_CAMERAS: {
    label: "确认本次内外摄像头角色",
    prompt: "请仔细查看上方两张本次临时画面。确认箱外/箱内角色完全正确？",
    parameters: () => ({
      reviewNonce: currentStatus.factoryTest.cameraReview.nonce,
      outsideRoleConfirmed: true,
      insideRoleConfirmed: true,
    }),
  },
  CHECK_UPGRADE_LINE: {
    label: "执行只读升级线路检查",
    prompt: "将发送 F2、切换 BOOT0/NRST 并只读识别 STM32 ROM；不会擦除或写入 Flash。确认设备周围安全？",
    parameters: () => ({ confirmReadOnlyBootloaderProbe: true }),
  },
  RUN_DELIVERY: {
    label: "执行离线投递硬件测试",
    prompt: "将真实发送一次 BB+AA，驱动屏幕、按钮、门锁/电机并等待 DD；命令绝不重发。确认周围无人且无阻挡？",
    parameters: () => ({ operatorAreaSafeConfirmed: true }),
  },
  RUN_CLEAN: {
    label: "执行离线清运硬件测试",
    prompt: "将真实发送一次 EE，驱动清运锁并等待 EF；门关闭必须在动作完成后另行确认。确认周围无人且无阻挡？",
    parameters: () => ({ operatorAreaSafeConfirmed: true }),
  },
  CONFIRM_CLEAN_DOOR: {
    label: "确认清运门已关闭",
    prompt: "请现场观察并手动确认清运门扇已经完全关闭。系统不会用电磁阀状态推定门位。确认后继续？",
    parameters: () => ({ cleanDoorClosedConfirmed: true }),
  },
  RECOVER: {
    label: "执行受控 MCU 恢复",
    prompt: "将停止继续下发、强制 BOOT0 回应用、复位 MCU，并重新验证原 F3/F1。若刚才是清运测试，请先现场确认门扇已关闭。确认执行？",
    parameters: () => ({
      confirmRecovery: true,
      cleanDoorClosedConfirmed: currentStatus.factoryTest.recovery?.context === "CLEAN",
    }),
  },
  FINALIZE: {
    label: "生成本地验收报告",
    prompt: "确认所有项目已由当前设备、当前镜像和当前 MCU 完成，并生成不可用于业务的本地验收报告？",
    parameters: () => ({ confirmFinalize: true }),
  },
};

const INSTRUCTIONS = {
  START: "首先建立当前镜像、硬件配置和 MCU 的独立验收运行。",
  RESTART_FAILED_RUN: "上次验收已终止失败，排除故障后可显式重新开始。",
  CHECK_MCU: "查询 F3 固件身份和 F1 传感器自检；仅 revision 2、STATUS=00 才能继续。",
  CAPTURE_EMPTY_WEIGHT: "清空承重面后，系统会在 3 秒内寻找连续 3 次、极差不超过 2 g 的稳定读数。",
  CAPTURE_LOADED_WEIGHT: "放置 500 g 砝码，稳定差分必须位于 490～510 g（包含边界）。",
  CONFIRM_WEIGHT_REMOVED: "取下砝码；稳定读数必须回到空载值 ±10 g。",
  CAPTURE_CAMERAS: "先拍摄本次临时画面；尚未查看画面时不能提前确认角色。",
  CONFIRM_CAMERAS: "确认请求只绑定当前 nonce；旧页面、旧画面或过期画面不能复用。",
  CHECK_UPGRADE_LINE: "只读检查 STM32F103C8 Device ID 0x0410，随后复位回原应用并复验 F3/F1。",
  RUN_DELIVERY: "真实命令最多写入一次；超时、损坏、迟到或重复 DD 都会进入恢复锁。",
  RUN_CLEAN: "真实命令最多写入一次；收到 EF 后仍需现场确认清运门关闭。",
  CONFIRM_CLEAN_DOOR: "请完成动作后的现场观察；提交前的门确认不会被接受。",
  RECOVER: "恢复不会重发 AA/EE。任何身份、自检或静默验证失败都会继续锁住设备。",
  FINALIZE: "报告只保存本地脱敏硬件结果，不写 EdgeStore、不上传照片、不联系后端。",
};

function formatBytes(value) {
  if (!Number.isFinite(value) || value < 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let amount = value;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount.toFixed(index < 2 ? 0 : 1)} ${units[index]}`;
}

function updateCapability(capability) {
  const card = document.querySelector(`[data-capability="${capability.id}"]`);
  if (!card) return;
  const state = card.querySelector('[data-role="state"]');
  if (state) state.textContent = capability.state || "NOT_RUN";
}

function updateActionConsole(status) {
  const factory = status.factoryTest || {};
  const allowed = Array.isArray(factory.allowedActions) ? factory.allowedActions : [];
  const action = allowed[0];
  const definition = ACTIONS[action];
  byId("executor-state").textContent = factory.executorAvailable
    ? factory.phase || factory.status || "READY"
    : "EXECUTOR OFFLINE";
  byId("action-instruction").textContent = definition
    ? INSTRUCTIONS[action]
    : factory.status === "PASSED"
      ? "本地验收已经通过，等待蜂窝、注册、云端机器验收和当前代次封存授权。"
      : "当前没有可执行步骤；请查看错误码或等待执行器恢复。";
  const button = byId("primary-action");
  button.dataset.operation = action || "";
  button.textContent = definition ? definition.label : "暂无可执行操作";
  button.disabled = requestRunning || !definition || !factory.executorAvailable;

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
  byId("stage").textContent = status.stage || "UNKNOWN";
  byId("release").textContent = status.image?.releaseId || "UNKNOWN";
  byId("machine").textContent = status.system?.machineSummary || "UNAVAILABLE";
  byId("observed-at").textContent = status.system?.observedAt || "—";
  byId("disk").textContent = formatBytes(status.system?.disk?.freeBytes);
  byId("test-status").textContent = status.factoryTest?.status || "NOT_RUN";

  if (Array.isArray(status.capabilities)) status.capabilities.forEach(updateCapability);
  const list = byId("hil-list");
  list.replaceChildren();
  for (const gate of Array.isArray(status.hardwareInLoopGates) ? status.hardwareInLoopGates : []) {
    const item = document.createElement("li");
    item.textContent = String(gate);
    list.appendChild(item);
  }
  updateActionConsole(status);
  byId("link-lamp").className = "link-state__lamp is-online";
  byId("link-state").textContent = "本地门户在线 · WAN 已隔离";
  byId("last-update").textContent = `最近刷新：${new Date().toLocaleTimeString("zh-CN")}`;
}

async function refreshStatus() {
  const button = byId("refresh");
  button.disabled = true;
  try {
    const response = await fetch("/api/v1/status", {
      method: "GET",
      cache: "no-store",
      credentials: "omit",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`HTTP_${response.status}`);
    updateStatus(await response.json());
  } catch (_error) {
    byId("link-lamp").className = "link-state__lamp is-error";
    byId("link-state").textContent = "无法读取本机状态";
    byId("last-update").textContent = "刷新失败；请确认仍连接 EcoBin 出厂热点";
  } finally {
    button.disabled = false;
  }
}

async function postAction(operation, parameters) {
  if (!currentStatus) return;
  requestRunning = true;
  updateActionConsole(currentStatus);
  byId("action-result").textContent = "操作执行中，请勿重复点击或断电……";
  try {
    const body = JSON.stringify({
      operation,
      expectedRevision: currentStatus.factoryTest.revision,
      parameters,
    });
    const response = await fetch("/api/v1/acceptance/action", {
      method: "POST",
      cache: "no-store",
      credentials: "omit",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-EcoBin-Factory-Action": "1",
      },
      body,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP_${response.status}`);
    byId("action-result").textContent = result.idempotent
      ? "重复请求未再次执行，已返回当前事实。"
      : "操作事实已保存。";
  } catch (error) {
    byId("action-result").textContent = `操作未完成：${error.message || "UNKNOWN"}`;
  } finally {
    requestRunning = false;
    await refreshStatus();
  }
}

async function performPrimaryAction() {
  const operation = byId("primary-action").dataset.operation;
  const definition = ACTIONS[operation];
  if (!definition || requestRunning || !window.confirm(definition.prompt)) return;
  await postAction(operation, definition.parameters());
}

async function confirmSeal() {
  if (!currentStatus || requestRunning) return;
  if (!window.confirm("这是单向离厂封存：成功后热点不会重新开放。确认当前设备可以离开工厂？")) return;
  const uid = crypto.randomUUID();
  requestRunning = true;
  byId("seal-result").textContent = "正在核对当前代次授权并执行封存……";
  try {
    const response = await fetch("/api/v1/acceptance/action", {
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
        parameters: { operatorConfirmationUid: uid },
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP_${response.status}`);
    byId("seal-result").textContent = "封存事实已可靠保存，热点将按状态机关闭。";
  } catch (error) {
    byId("seal-result").textContent = `封存未完成：${error.message || "UNKNOWN"}`;
  } finally {
    requestRunning = false;
    await refreshStatus();
  }
}

byId("refresh").addEventListener("click", refreshStatus);
byId("primary-action").addEventListener("click", performPrimaryAction);
byId("seal-confirm").addEventListener("click", confirmSeal);
refreshStatus();
window.setInterval(() => {
  if (!requestRunning) refreshStatus();
}, 5000);

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
  LOCAL_HARDWARE_ACCEPTANCE: { number: "02", title: "本机硬件检查", instruction: "按页面按钮完成当前设备的本地硬件检查。" },
  CELLULAR_AND_TIME: { number: "03", title: "蜂窝联网与时间校准", instruction: "设备正在通过蜂窝通信模块建立网络并校准系统时间。" },
  ENROLLMENT_AND_CREDENTIALS: { number: "04", title: "设备注册与联网资料保存", instruction: "设备正在完成首次注册，并安全保存日常联网资料。" },
  RUNTIME_AND_MQTT: { number: "05", title: "启动设备服务并连接云端", instruction: "设备正在交接控制板通信、启动日常服务并连接云端设备平台。" },
  FACTORY_BAGS: { number: "06", title: "登记初始回收袋", instruction: "请在厂家小程序扫描设备二维码，再逐一扫描每个投口的初始袋码。" },
  CLOUD_EVIDENCE: { number: "07", title: "采集并上传验收信息", instruction: "设备正在检查运行状态、拍照、上传验收照片，并把结果可靠送达后台。" },
  CLOUD_DECISION_AND_AUTHORIZATION: { number: "08", title: "后台验收与封存授权", instruction: "等待后台确认本次设备功能检查，并向设备下发结束出厂模式的授权。" },
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

const FIRST_BOOT_STAGE_LABELS = Object.freeze({
  SYSTEM_PREPARED: "系统准备完成",
  FACTORY_PORTAL_READY: "出厂页面已就绪",
  FACTORY_TEST_REQUIRED: "等待本机硬件检查",
  FACTORY_TEST_RUNNING: "正在进行本机硬件检查",
  FACTORY_TEST_PASSED: "本机硬件检查已通过",
  UPLINK_REQUIRED: "等待蜂窝联网",
  UPLINK_READY: "蜂窝网络已连接",
  ENROLLMENT_REQUIRED: "正在注册设备",
  ENROLLMENT_COMPLETE: "设备注册已完成",
  SEALED: "出厂模式已关闭",
  COMPLETE: "接入准备已完成",
});

const FACTORY_TEST_STATUS_LABELS = Object.freeze({
  NOT_RUN: "尚未开始",
  RUNNING: "检查中",
  PASSED: "已通过",
  FAILED: "未通过",
  RECOVERY_REQUIRED: "需要完成安全恢复",
});

const FACTORY_SEAL_STATUS_LABELS = Object.freeze({
  CLOUD_ACCEPTANCE_REQUIRED: "等待后台验收与授权",
  IMAGE_RELEASE_INVALID: "设备软件版本信息不完整，暂时不能结束出厂模式",
  FACTORY_REPORT_INVALID: "本机硬件检查报告无效，暂时不能结束出厂模式",
  FACTORY_SEAL_LOCAL_FACT_CHANGED: "本机检查记录已变化，需要重新核对",
  ENROLLMENT_CLEANUP_REQUIRED: "首次注册的临时材料尚未清理完成",
  DEVICE_CREDENTIALS_INVALID: "日常联网资料缺失或无效",
  RUNTIME_NOT_HEALTHY: "设备日常服务尚未就绪",
  HANDOFF_SAFE_REQUIRED: "控制板通信尚未安全交接",
  DEVICE_CAPABILITIES_INVALID: "设备能力信息缺失或无效",
  MAINTENANCE_BUSY: "设备正在维护，暂时不能结束出厂模式",
  SEAL_READY: "可以结束出厂模式",
  SEALED_RESPONSE_PENDING: "已保存结果，正在通知页面",
  SEALED_CLEANUP_PENDING: "已保存结果，正在关闭出厂服务",
  SEALED: "出厂模式已关闭",
  SEALED_FACT_MISSING: "封存记录不完整，需要技术人员处理",
  SEALED_FACT_INVALID: "封存记录校验失败，需要技术人员处理",
  FACTORY_SEAL_NOT_AVAILABLE: "暂时无法读取封存授权",
  FACTORY_SEAL_RESPONSE_INVALID: "封存授权响应异常",
});

const FACTORY_PHASE_LABELS = Object.freeze({
  NOT_RUN: "等待开始",
  STARTED: "本轮检查已开始",
  EXECUTOR_UNAVAILABLE: "硬件检查服务暂不可用",
  MCU_CHECK_FAILED: "控制板与传感器检查未通过",
  MCU_CHECK_PASSED: "控制板与传感器检查已通过",
  WAITING_FOR_500G_LOAD: "等待放置 500 克砝码",
  WAITING_FOR_WEIGHT_REMOVAL: "等待取下 500 克砝码",
  WEIGHT_CHECK_FAILED: "称重检查未通过",
  WEIGHT_CHECK_PASSED: "称重检查已通过",
  WAITING_FOR_CAMERA_ROLE_CONFIRMATION: "等待确认两路摄像头位置",
  CAMERA_CHECK_FAILED: "摄像头检查未通过",
  CAMERA_CHECK_PASSED: "摄像头检查已通过",
  LEGACY_UPDATE_LINE_SELECTION_REQUIRED: "请重新选择远程升级线路装配情况",
  LEGACY_ACTION_SAFETY_CONFIRMATION_REQUIRED: "请重新完成现场安全确认",
  HARDWARE_CONFIG_CHANGED: "硬件配置已变化，请重新开始检查",
  F2_COMMAND_MAY_HAVE_BEEN_SENT: "控制板升级准备状态需要安全恢复",
  F2_PREPARED: "控制板已进入升级准备状态",
  ENTERING_STM32_ROM: "正在检查控制板远程升级线路",
  STM32_ROM_MAY_BE_ACTIVE: "正在确认控制板是否仍处于升级模式",
  RETURNING_TO_APPLICATION: "正在恢复控制板日常程序",
  UPGRADE_LINE_PASSED: "控制板远程升级线路检查已通过",
  DELIVERY_AWAITING_AREA_CONFIRMATION: "等待确认投递区域已经恢复安全",
  CLEAN_AWAITING_DOOR_CONFIRMATION: "等待现场确认清运门已经关闭",
  CLEAN_SAFE_VERIFIED: "清运门关闭情况已确认",
  DELIVERY_SAFE_VERIFIED: "投递区域安全情况已确认",
  LEGACY_ACTION_SAFETY_FAILURE_READY: "旧检查记录缺少现场安全确认，请先保存本轮失败报告",
  FINALIZING_REPORT: "正在保存本机硬件检查报告",
  COMPLETE: "本机硬件检查已完成",
});

const RECOVERY_CONTEXT_LABELS = Object.freeze({
  DELIVERY: "投递机构",
  CLEAN: "清运门锁",
  UPGRADE_LINE: "控制板远程升级线路",
  MCU: "设备控制板",
});

const STEP_LABELS = Object.freeze({
  SYSTEM_PREPARED: "系统与磁盘准备",
  FACTORY_PORTAL_READY: "出厂热点与网页",
  MCU: "控制板通信和自检",
  WEIGHT: "称重检查",
  MCU_UPDATE_LINE: "升级线路",
  CAMERAS: "双摄检查",
  DELIVERY: "投递动作",
  CLEAN: "清运动作",
  REPORT: "本机硬件检查报告",
  AIR780E_PROFILE: "蜂窝网络配置",
  CELLULAR_UPLINK: "蜂窝网络连接",
  TRUSTED_TIME: "系统时间",
  IDENTITY_PREPARED: "设备身份准备",
  CHALLENGE_ACQUIRED: "取得一次性注册许可",
  REQUEST_SUBMITTED: "提交注册请求",
  CREDENTIALS_INSTALLED: "日常联网资料已保存",
  K1_REMOVED: "清理一次性注册材料",
  UART_HANDOFF: "控制板通信安全交接",
  HARDWARE_SERVICE: "设备日常服务",
  UART_READY: "控制板通信就绪",
  MQTT_CONNECTED: "云端设备平台连接",
  DEVICE_ENTRY_URL: "设备扫码入口",
  P8_REQUEST: "收到云端验收请求",
  STORE_CHECK: "本地存储",
  CONFIG_CHECK: "配置安全保存",
  MCU_AND_SENSORS: "控制板与传感器",
  CAMERA_CAPTURE: "双摄拍照",
  COS_UPLOAD_READBACK: "验收照片上传与校验",
  EVIDENCE_RECORDED: "验收信息已安全保存",
  ONENET_TRANSPORT_ACCEPTED: "云端设备平台已接收",
  PLATFORM_CONFIRMED: "后台确认接收",
  EVIDENCE_CONFIRMED: "后台已确认验收信息",
  CURRENT_GENERATION_AUTHORIZED: "本次验收的封存授权",
  SEAL_AUTHORIZATION: "封存授权",
  LOCAL_CONFIRMATION_AVAILABLE: "允许人工确认",
  SEAL_MARKER_SAVED: "封存结果已安全保存",
});

const DETAIL_DESCRIPTIONS = Object.freeze({
  BOOT_READY: "系统准备和出厂热点均已就绪。",
  NOT_RUN: "等待操作员开始本机硬件检查。",
  COMPLETE: "本机硬件检查已经通过。",
  CELLULAR_AND_TIME_READY: "蜂窝网络可用，系统时间已经校准。",
  TIME_SYNC_PENDING: "蜂窝网络已建立，正在完成系统时间校准。",
  ENROLLMENT_COMPLETE: "日常联网资料已经保存，一次性注册材料和临时文件已清理。",
  IDENTITY_PREPARATION: "正在准备设备身份和注册材料。",
  CHALLENGE_REQUEST: "正在向后台申请一次性注册许可。",
  ENROLLMENT_SUBMISSION: "正在提交设备注册请求。",
  CREDENTIAL_INSTALLATION: "后台已接受注册，正在验证并保存日常联网资料。",
  K1_CLEANUP: "日常联网资料已保存，正在删除一次性注册材料和临时文件。",
  RETRY_WAIT: "本次注册未成功，系统会自动重试。",
  RUNTIME_READY: "控制板通信已经安全交接，设备日常服务和云端连接均正常。",
  RUNTIME_STATUS_STALE: "设备日常服务的状态超过 15 秒没有更新。",
  WAITING_ONENET_ACCEPTANCE: "验收信息已可靠保存，正在等待云端设备平台接收。",
  WAITING_BACKEND_CONFIRMATION: "云端设备平台已接收，正在等待后台确认。",
  SCAN_DEVICE_AND_FACTORY_BAGS: "请切换到厂家小程序，扫描设备和每个投口的初始袋码。",
  P8_REQUEST_RECEIVED: "初始袋码已经登记完整，后台已发起云端自动验收。",
  REQUEST_RECEIVED: "设备已经收到云端验收请求，正在开始采集验收信息。",
  PERSISTENT_STORE_CHECK: "正在检查本地数据能否安全保存。",
  CONFIGURATION_CHECK: "正在核对当前设备配置。",
  MCU_SENSOR_CHECK: "正在检查控制板和传感器。",
  CAMERA_CAPTURE: "正在使用双摄拍摄验收照片。",
  COS_UPLOAD_READBACK: "正在上传验收照片并确认照片可以正常读取。",
  EVIDENCE_PERSISTENCE: "正在把本次验收信息安全保存到设备。",
  EVIDENCE_RECORDED: "验收信息已经安全保存，正在等待送达后台。",
  EVIDENCE_CONFIRMED: "验收信息已可靠送达并由后台确认接收。",
  WAITING_CLOUD_DECISION: "验收信息已送达，正在等待后台确认结果并发送封存授权。若长时间没有推进，请查看后台设备详情。",
  CLOUD_ACCEPTANCE_PASSED: "后台已确认本次设备功能检查通过，结束出厂模式的授权已经到达设备。",
  SEAL_READY: "所有封存前置条件已满足，请确认结束出厂模式。",
  SEALED_RESPONSE_PENDING: "封存结果已经安全保存，正在把最终结果交给本页面。",
  SEALED_CLEANUP_PENDING: "封存结果已经保存，正在关闭热点和出厂服务。",
  SEALED: "设备已永久结束出厂模式。",
  STATUS_UNAVAILABLE: "暂时无法取得这一阶段的状态。",
  FACTORY_SEAL_NOT_AVAILABLE: "暂时无法读取封存授权状态。",
});

const ERROR_DESCRIPTIONS = Object.freeze({
  NONE: "当前没有错误",
  STATUS_UNAVAILABLE: "状态来源暂时不可用",
  SYSTEM_FACTS_INVALID: "无法读取设备基础状态",
  FACTORY_TEST_GATE_CLOSED: "本机硬件检查尚未完成",
  FACTORY_TEST_FAILED: "本机硬件检查未通过",
  FACTORY_STATE_INVALID: "本机硬件检查记录状态不完整",
  FACTORY_RECOVERY_REQUIRED: "上次硬件动作需要先完成安全恢复",
  FACTORY_REPORT_INVALID: "本机硬件检查报告缺失或校验失败",
  IMAGE_RELEASE_INVALID: "设备软件版本信息缺失或校验失败",
  FACTORY_SEAL_LOCAL_FACT_CHANGED: "结束出厂前，本机检查记录发生了变化",
  ENROLLMENT_CLEANUP_REQUIRED: "首次注册的临时材料尚未清理完成",
  DEVICE_CREDENTIALS_INVALID: "日常联网资料缺失或校验失败",
  DEVICE_CAPABILITIES_INVALID: "设备能力信息缺失或校验失败",
  MAINTENANCE_BUSY: "设备正在维护，请等待维护结束",
  CLOUD_ACCEPTANCE_REQUIRED: "正在等待后台完成验收并发送授权",
  HANDOFF_SAFE_REQUIRED: "控制板通信尚未完成安全交接",
  RUNTIME_NOT_HEALTHY: "设备日常服务尚未达到可封存状态",
  SEALED_FACT_MISSING: "设备的封存记录不完整",
  SEALED_FACT_INVALID: "设备的封存记录校验失败",
  FACTORY_SEAL_NOT_AVAILABLE: "暂时无法读取封存授权状态",
  TIME_SYNC_PENDING: "正在等待系统时间校准完成",
  TIME_TRUST_QUERY_FAILED: "无法读取系统时间同步状态",
  CHRONY_ONLINE_FAILED: "无法启用网络时间源",
  CHRONY_ACTIVITY_FAILED: "无法读取网络时间源状态",
  CHRONY_SOURCES_UNAVAILABLE: "没有可用的网络时间源",
  CHRONY_REFRESH_FAILED: "刷新网络时间源失败",
  CHRONY_BURST_FAILED: "发起快速校时失败",
  CHRONY_WAITSYNC_FAILED: "等待校时结果失败",
  TIME_SYNC_INTERNAL_ERROR: "校时服务内部错误",
  TIME_NOT_TRUSTED: "系统时间尚未校准完成",
  CELLULAR_DEVICE_NOT_FOUND: "没有检测到蜂窝通信模块",
  CELLULAR_CONFIG_MISSING: "蜂窝网络配置缺失",
  CELLULAR_CONFIG_NOT_REGULAR: "蜂窝网络配置文件类型不安全",
  CELLULAR_CONFIG_PERMISSIONS: "蜂窝网络配置文件权限不安全",
  CELLULAR_CONFIG_SIZE: "蜂窝网络配置文件大小异常",
  CELLULAR_CONFIG_ENCODING: "蜂窝网络配置文件编码异常",
  CELLULAR_CONFIG_SYNTAX: "蜂窝网络配置格式错误",
  CELLULAR_CONFIG_FIELDS: "蜂窝网络配置内容不完整或重复",
  CELLULAR_CONFIG_VALUE: "蜂窝网络配置包含无效内容",
  CELLULAR_CONFIG_SCHEMA: "蜂窝网络配置版本不匹配",
  CELLULAR_HIL_LOCKED: "当前批次尚未完成蜂窝联网配置验证",
  CELLULAR_APN_MODE_UNSUPPORTED: "当前蜂窝联网方式不受支持",
  CELLULAR_CONNECTION_ID_INVALID: "蜂窝网络连接名称无效",
  CELLULAR_USB_DRIVER_UNKNOWN: "蜂窝通信模块驱动配置不受支持",
  CELLULAR_USB_PROFILE_UNKNOWN: "蜂窝通信模块连接方式配置不受支持",
  CELLULAR_PROBE_IPV4_INVALID: "联网检测地址配置无效",
  CELLULAR_HTTPS_PROBE_INVALID: "联网检测服务地址配置无效",
  CELLULAR_RNDIS_AMBIGUOUS: "检测到多个蜂窝网络接口，设备无法安全选择",
  CELLULAR_USB_PARENT_UNVERIFIED: "无法确认蜂窝网络接口来自指定通信模块",
  CELLULAR_USB_DRIVER_MISMATCH: "蜂窝通信模块驱动与当前配置不一致",
  CELLULAR_RNDIS_UNAVAILABLE: "未检测到蜂窝通信模块提供的网络接口",
  CELLULAR_INTERFACE_INVALID: "蜂窝网络接口配置异常",
  CELLULAR_SIM_ABSENT: "未检测到物联网卡",
  CELLULAR_SIM_LOCKED: "物联网卡已锁定",
  CELLULAR_DHCP_UNAVAILABLE: "蜂窝网络尚未取得设备地址",
  CELLULAR_DEFAULT_ROUTE_WRONG_INTERFACE: "设备流量未按要求使用蜂窝网络",
  CELLULAR_DNS_UNAVAILABLE: "蜂窝网络暂时无法解析后台地址",
  CELLULAR_HTTPS_UNAVAILABLE: "蜂窝网络暂时无法连接后台服务",
  CELLULAR_PROFILE_INSTALL_FAILED: "蜂窝网络连接配置保存失败",
  CELLULAR_ACTIVATION_FAILED: "蜂窝网络连接未能激活",
  CELLULAR_EGRESS_GATE_FAILED: "联网安全规则未能正确启用",
  ACTIVE_SWAP_DETECTED: "检测到设备注册资料被替换",
  BACKEND_URL_MISSING: "设备注册后台地址未配置",
  CHALLENGE_EXPIRED: "一次性注册许可已经过期",
  CHALLENGE_RESPONSE_INVALID: "后台返回的一次性注册许可无效",
  CREDENTIAL_INSTALL_FAILED: "日常联网资料保存失败",
  ENROLLMENT_NETWORK_UNAVAILABLE: "设备注册时无法连接后台",
  ENROLLMENT_BACKEND_TEMPORARY: "后台暂时无法完成设备注册",
  ENROLLMENT_BACKEND_REJECTED: "后台拒绝了设备注册",
  ENROLLMENT_INTERNAL_ERROR: "设备注册服务内部错误",
  ENROLLMENT_KEY_INVALID: "设备注册密钥无效",
  ENROLLMENT_PENDING: "设备注册尚未完成",
  ENROLLMENT_RESPONSE_INVALID: "后台返回的注册结果无效",
  ENROLLMENT_STATE_INVALID: "本地注册状态无效",
  ENROLLMENT_STATE_PERMISSIONS_INVALID: "本地注册状态文件权限不安全",
  K1_CLEANUP_FAILED: "日常联网资料已保存，但清理一次性注册材料失败",
  RUNTIME_STATUS_STALE: "正式硬件服务状态已经过期",
  RUNTIME_BOOT_FAILED: "设备日常服务启动失败",
  RUNTIME_SERVICE_STOPPED: "设备日常服务已经停止",
  MQTT_CONNECT_FAILED: "设备连接云端平台失败",
  MQTT_DISCONNECTED: "设备与云端平台的连接已经断开",
  MQTT_FAILED: "设备与云端平台通信失败",
  UART_HANDSHAKE_FAILED: "设备控制板通信确认失败",
  UART_OPEN_FAILED: "无法建立设备控制板通信",
  UART_QUERY_STATE_FAILED: "无法读取设备控制板状态",
  UART_RECOVERY_FAILED: "设备控制板通信恢复失败",
  UART_FAILED: "设备控制板通信失败",
  UART_DISCONNECTED: "设备控制板连接已经断开",
  P8_STORAGE_CHECK_FAILED: "本地存储检查失败",
  P8_CONFIGURATION_CHECK_FAILED: "设备配置检查失败",
  P8_MCU_SENSOR_CHECK_FAILED: "控制板或传感器检查失败",
  P8_CAMERA_CAPTURE_FAILED: "验收照片拍摄失败",
  P8_COS_UPLOAD_READBACK_FAILED: "验收照片上传或读取校验失败",
  P8_EVIDENCE_PERSISTENCE_FAILED: "验收信息安全保存失败",
  P8_DEVICE_ENTRY_URL_FAILED: "设备扫码入口地址检查失败",
  P8_GRANT_NOT_AVAILABLE: "尚未取得验收照片上传授权",
  P8_EXECUTION_FAILED: "云端自动验收执行失败",
  ACCEPTANCE_EVIDENCE_DELIVERY_DEAD: "验收信息多次发送失败",
  MCU_RESET_LINE_REQUIRED_FOR_RECOVERY: "设备控制板复位线路未安装，无法完成安全恢复",
  APPLICATION_RECOVERY_FAILED: "设备控制板未能恢复到日常运行程序",
});

const CELLULAR_CONFIGURATION_ERRORS = new Set([
  "CELLULAR_CONFIG_MISSING",
  "CELLULAR_CONFIG_NOT_REGULAR",
  "CELLULAR_CONFIG_PERMISSIONS",
  "CELLULAR_CONFIG_SIZE",
  "CELLULAR_CONFIG_ENCODING",
  "CELLULAR_CONFIG_SYNTAX",
  "CELLULAR_CONFIG_FIELDS",
  "CELLULAR_CONFIG_VALUE",
  "CELLULAR_CONFIG_SCHEMA",
  "CELLULAR_HIL_LOCKED",
  "CELLULAR_APN_MODE_UNSUPPORTED",
  "CELLULAR_CONNECTION_ID_INVALID",
  "CELLULAR_USB_DRIVER_UNKNOWN",
  "CELLULAR_USB_PROFILE_UNKNOWN",
  "CELLULAR_PROBE_IPV4_INVALID",
  "CELLULAR_HTTPS_PROBE_INVALID",
]);

const CELLULAR_MODULE_ERRORS = new Set([
  "CELLULAR_DEVICE_NOT_FOUND",
  "CELLULAR_RNDIS_UNAVAILABLE",
]);

const CELLULAR_MODULE_IDENTITY_ERRORS = new Set([
  "CELLULAR_USB_PARENT_UNVERIFIED",
  "CELLULAR_USB_DRIVER_MISMATCH",
]);

const CELLULAR_SIM_ERRORS = new Set([
  "CELLULAR_SIM_ABSENT",
  "CELLULAR_SIM_LOCKED",
]);

const CELLULAR_ADDRESS_ERRORS = new Set([
  "CELLULAR_DHCP_UNAVAILABLE",
]);

const CELLULAR_ROUTE_ERRORS = new Set([
  "CELLULAR_INTERFACE_INVALID",
  "CELLULAR_DEFAULT_ROUTE_WRONG_INTERFACE",
]);

const CELLULAR_REACHABILITY_ERRORS = new Set([
  "CELLULAR_DNS_UNAVAILABLE",
  "CELLULAR_HTTPS_UNAVAILABLE",
]);

const TIME_SYNC_ERRORS = new Set([
  "TIME_NOT_TRUSTED",
  "TIME_SYNC_PENDING",
  "TIME_TRUST_QUERY_FAILED",
  "CHRONY_ONLINE_FAILED",
  "CHRONY_ACTIVITY_FAILED",
  "CHRONY_SOURCES_UNAVAILABLE",
  "CHRONY_REFRESH_FAILED",
  "CHRONY_BURST_FAILED",
  "CHRONY_WAITSYNC_FAILED",
  "TIME_SYNC_INTERNAL_ERROR",
]);

const ACTION_ERROR_DESCRIPTIONS = Object.freeze({
  ACCEPTANCE_EXECUTOR_BUSY: "另一项硬件操作正在执行，请等待完成后再试。",
  ACCEPTANCE_EXECUTOR_NOT_OPEN: "本机硬件检查服务尚未启动，请刷新页面后重试。",
  ACCEPTANCE_EXECUTOR_UNAVAILABLE: "本机硬件检查服务暂时不可用。",
  ACCEPTANCE_RECOVERY_REQUIRED: "设备需要先完成页面提示的安全恢复。",
  ACCEPTANCE_REVISION_CONFLICT: "设备状态已经更新，请刷新页面后按最新提示操作。",
  ACTION_NOT_ALLOWED_IN_CURRENT_STATE: "当前状态不允许执行这项操作，请刷新页面查看下一步。",
  ACTION_NOT_SUPPORTED: "当前软件不支持这项操作。",
  ACTION_PARAMETERS_INVALID: "操作信息不完整，请刷新页面后重新操作。",
  ANOTHER_FACTORY_ACTION_IS_ACTIVE: "另一项硬件操作正在执行，请等待完成后再试。",
  CAMERA_REVIEW_EXPIRED: "摄像头确认画面已经过期，请重新拍摄。",
  CAMERA_REVIEW_NOT_FOUND: "尚未找到可确认的摄像头画面，请重新拍摄。",
  CLEAN_DOOR_CONFIRMATION_REQUIRED: "请先现场确认清运门已经完全关闭。",
  DELIVERY_AREA_SAFETY_CONFIRMATION_REQUIRED: "请先现场确认投递区域已经恢复安全。",
  EXISTING_ACCEPTANCE_RUN_NOT_FINISHED: "上一轮本机硬件检查尚未结束。",
  FACTORY_SEAL_ALREADY_COMMITTED: "设备已经结束出厂模式。",
  FACTORY_SEAL_NOT_AUTHORIZED: "后台尚未授权设备结束出厂模式。",
  LOCAL_ACCEPTANCE_NOT_PASSED: "本机硬件检查尚未通过。",
  MAINTENANCE_BUSY: "设备正在维护，请等待维护结束后再试。",
  MCU_IDENTITY_CHANGED_AFTER_ACTION: "硬件动作后控制板身份发生变化，需要安全恢复。",
  MCU_IDENTITY_CHANGED_DURING_RECOVERY: "安全恢复过程中控制板身份发生变化，需要技术人员检查。",
  NO_ACCEPTANCE_RECOVERY_REQUIRED: "设备当前不需要执行安全恢复。",
  RUNTIME_NOT_HEALTHY: "设备日常服务尚未达到可封存状态。",
  SEAL_READY: "设备已经可以结束出厂模式。",
});

const ACTIONS = Object.freeze({
  START: {
    label: "开始本机硬件检查",
    prompt: "确认设备周围无人、日常设备服务未运行，并开始本机硬件检查？",
    parameters: () => ({ confirmOfflineAcceptance: true, mcuUpdateLineInstalled: selectedUpdateLineState() }),
  },
  RESTART_FAILED_RUN: {
    label: "重新开始本机硬件检查",
    prompt: "确认故障已经排除，并重新开始本机硬件检查？",
    parameters: () => ({ confirmRestartFailedAcceptance: true, mcuUpdateLineInstalled: selectedUpdateLineState() }),
  },
  CHECK_MCU: { label: "检查控制板与传感器", prompt: "确认设备当前没有进行投递或清运动作？", parameters: () => ({}) },
  CAPTURE_EMPTY_WEIGHT: { label: "采集稳定空载重量", prompt: "请清空承重面。确认当前没有砝码或测试物？", parameters: () => ({ confirmScaleEmpty: true }) },
  CAPTURE_LOADED_WEIGHT: { label: "采集 500 克重量", prompt: "请将 500 克砝码放稳。确认已正确放置？", parameters: () => ({ confirm500gPlaced: true }) },
  CONFIRM_WEIGHT_REMOVED: { label: "确认砝码已取下", prompt: "请取下 500 克砝码。确认承重面已经恢复空载？", parameters: () => ({ confirm500gRemoved: true }) },
  CAPTURE_CAMERAS: { label: "拍摄双摄确认图", prompt: "将立即使用两台摄像头拍摄临时画面。确认继续？", parameters: () => ({ confirmCaptureNow: true }) },
  CONFIRM_CAMERAS: {
    label: "确认摄像头角色",
    prompt: "确认上方箱外、箱内画面与实际安装位置完全一致？",
    parameters: () => ({ reviewNonce: currentStatus.factoryTest.cameraReview.nonce, outsideRoleConfirmed: true, insideRoleConfirmed: true }),
  },
  CHECK_UPGRADE_LINE: { label: "检查控制板远程升级线路", prompt: "这项操作只检查远程升级能力，不会改写控制板程序。确认设备周围安全？", parameters: () => ({ confirmReadOnlyBootloaderProbe: true }) },
  RUN_DELIVERY: { label: "执行投递硬件测试", prompt: "将真实驱动投递机构。确认周围无人、机构无阻挡？", parameters: () => ({ operatorAreaSafeConfirmed: true }) },
  CONFIRM_DELIVERY_AREA_SAFE: { label: "确认投递区域安全", prompt: "请现场检查机构已停止、周围无人且没有阻挡物。确认安全？", parameters: () => ({ operatorAreaSafeConfirmed: true }) },
  RUN_CLEAN: { label: "执行清运硬件测试", prompt: "将真实驱动清运锁。确认周围无人、机构无阻挡？", parameters: () => ({ operatorAreaSafeConfirmed: true }) },
  CONFIRM_CLEAN_DOOR: { label: "确认清运门已关闭", prompt: "请现场观察并确认清运门已经完全关闭。", parameters: () => ({ cleanDoorClosedConfirmed: true }) },
  RECOVER: {
    label: "执行受控恢复",
    prompt: ({ cleanRecovery }) => cleanRecovery
      ? "清运门没有门位传感器。请现场观察并确认清运门已经完全关闭；确认后系统将复位设备控制板并重新验证。"
      : "系统将停止继续发送指令、复位设备控制板并重新验证。确认执行？",
    parameters: ({ cleanRecovery }) => ({ confirmRecovery: true, cleanDoorClosedConfirmed: cleanRecovery }),
  },
  FINALIZE: {
    label: "生成本机硬件检查报告",
    prompt: "确认所有本机硬件项目均已完成，并生成检查报告？",
    parameters: () => ({
      confirmFinalize: true,
      ...(currentStatus.factoryTest.mcuPeripheralEvidenceMode === "SIMULATED_PERIPHERALS" ? { confirmSimulatedPeripheralEvidence: true } : {}),
    }),
  },
});

const ACTION_INSTRUCTIONS = Object.freeze({
  START: "选择控制板远程升级线路的实际装配情况，然后开始本机硬件检查。",
  RESTART_FAILED_RUN: "排除上次错误后，重新开始一轮独立检查。",
  CHECK_MCU: "系统将查询设备控制板身份和传感器自检结果。",
  CAPTURE_EMPTY_WEIGHT: "清空承重面，等待系统取得稳定空载值。",
  CAPTURE_LOADED_WEIGHT: "放置 500 克砝码并保持稳定。",
  CONFIRM_WEIGHT_REMOVED: "取下砝码，确认称重恢复到空载范围。",
  CAPTURE_CAMERAS: "拍摄本次临时画面，用于确认箱外和箱内摄像头。",
  CONFIRM_CAMERAS: "查看两张临时画面，确认摄像头角色正确。",
  CHECK_UPGRADE_LINE: "只读检查远程升级线路，完成后控制板会返回日常程序。",
  RUN_DELIVERY: "操作会真实驱动机构；开始前确认人员和障碍物已经离开。",
  CONFIRM_DELIVERY_AREA_SAFE: "动作结束后重新检查现场，再确认区域安全。",
  RUN_CLEAN: "操作会真实驱动清运锁；开始前确认现场安全。",
  CONFIRM_CLEAN_DOOR: "动作结束后现场确认清运门已经完全关闭。",
  RECOVER: "恢复不会重发投递或清运命令，只复位并重新验证设备控制板。",
  FINALIZE: "保存本轮本机硬件检查报告，随后自动进入联网注册阶段。",
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

function mappedLabel(labels, value, fallback) {
  return typeof value === "string" && labels[value] ? labels[value] : fallback;
}

function firstBootStageLabel(value) {
  return mappedLabel(FIRST_BOOT_STAGE_LABELS, value, "接入状态待确认");
}

function factoryTestStatusLabel(value) {
  return mappedLabel(FACTORY_TEST_STATUS_LABELS, value, "检查状态待确认");
}

function factorySealStatusLabel(value) {
  return mappedLabel(FACTORY_SEAL_STATUS_LABELS, value, "出厂模式状态待确认");
}

function factoryPhaseLabel(value) {
  if (typeof value !== "string") return "正在读取检查状态";
  if (FACTORY_PHASE_LABELS[value]) return FACTORY_PHASE_LABELS[value];
  if (value.endsWith("_ARMED")) return "硬件操作已经准备，等待执行";
  if (value.endsWith("_WAITING_FINAL_RESULT")) return "硬件已开始动作，正在等待完成结果";
  if (value.endsWith("_COMMAND_MAY_HAVE_BEEN_SENT")) return "正在确认硬件动作后的安全状态";
  if (value.endsWith("_RESULT_RECORDED")) return "硬件动作结果已经保存";
  if (value.endsWith("_RECOVERED_SAFE")) return "硬件已经恢复到安全状态";
  if (value.endsWith("_INTERRUPTED_BEFORE_COMMAND")) return "硬件动作尚未发出，本轮操作已经停止";
  return "正在核对本机硬件检查状态";
}

function mappedErrorDescription(code) {
  return ERROR_DESCRIPTIONS[code]
    || ACTION_ERROR_DESCRIPTIONS[code]
    || FACTORY_SEAL_STATUS_LABELS[code];
}

function errorDescription(code) {
  return mappedErrorDescription(code)
    || "设备报告了需要技术人员确认的问题";
}

function technicalCodeText(code) {
  return !code || code === "NONE" || code === "UNKNOWN" ? "无" : code;
}

function releaseLabel(releaseId) {
  return !releaseId || releaseId === "UNKNOWN" ? "无法读取设备软件版本" : releaseId;
}

function actionErrorDescription(code) {
  if (code === "REQUEST_TIMEOUT") return "设备响应较慢，系统正在自动核实最新状态";
  if (code === "BROWSER_RANDOM_UNAVAILABLE") return "当前浏览器不支持安全确认，请更换浏览器";
  if (typeof code === "string" && code.startsWith("HTTP_")) {
    return "设备网页暂时无法完成请求，请刷新后按最新提示重试";
  }
  return ACTION_ERROR_DESCRIPTIONS[code]
    || ERROR_DESCRIPTIONS[code]
    || "本次操作未完成，请刷新页面后按最新提示重试";
}

function describeDetail(node) {
  if (DETAIL_DESCRIPTIONS[node.detailCode]) return DETAIL_DESCRIPTIONS[node.detailCode];
  const meta = NODE_META[node.id];
  if (node.state === "COMPLETED") return `${meta?.title || "当前阶段"}已完成。`;
  return meta?.instruction || "请查看诊断信息并等待状态更新。";
}

function blockedStepProblem(node) {
  return (Array.isArray(node.steps) ? node.steps : []).find(
    (step) => step.state === "BLOCKED"
      && step.errorCode
      && step.errorCode !== "NONE",
  );
}

function blockedStepErrorCode(node) {
  return blockedStepProblem(node)?.errorCode;
}

function factoryRecoveryProblem(node) {
  if (node.id !== "LOCAL_HARDWARE_ACCEPTANCE") return null;
  const factoryTest = currentStatus?.factoryTest;
  const recovery = factoryTest?.recovery;
  const code = recovery?.resultCode;
  if (!code || code === "NONE") return null;
  return {
    code: factoryTest.recovery.resultCode,
    context: mappedLabel(
      RECOVERY_CONTEXT_LABELS,
      recovery.context,
      "当前硬件操作",
    ),
  };
}

function describeError(node) {
  const recovery = factoryRecoveryProblem(node);
  const blockedStep = blockedStepProblem(node);
  const code = recovery?.code
    || blockedStep?.errorCode
    || (node.errorCode && node.errorCode !== "NONE" ? node.errorCode : node.detailCode);
  const description = mappedErrorDescription(code)
    || (recovery
      ? `${recovery.context}需要先完成安全恢复`
      : blockedStep
        ? `${STEP_LABELS[blockedStep.id] || "对应检查项目"}未通过`
        : "设备报告了需要技术人员确认的问题");
  return { code, description };
}

function recoveryInstruction(node, code) {
  if (node.id === "CELLULAR_AND_TIME") {
    if (CELLULAR_CONFIGURATION_ERRORS.has(code)) {
      return "设备的蜂窝网络配置缺失或无效，请联系技术人员检查当前镜像配置。";
    }
    if (CELLULAR_MODULE_ERRORS.has(code)) {
      return "检查蜂窝通信模块的供电和连接线；重新插稳后，设备会自动重试。";
    }
    if (code === "CELLULAR_RNDIS_AMBIGUOUS") {
      return "设备检测到多个蜂窝网络接口，请移除非本机配置的通信模块后重试。";
    }
    if (CELLULAR_MODULE_IDENTITY_ERRORS.has(code)) {
      return "蜂窝通信模块的联网模式或驱动与当前镜像不匹配，请联系技术人员检查模块模式、驱动和镜像配置。";
    }
    if (code === "CELLULAR_PROFILE_INSTALL_FAILED") {
      return "系统无法写入蜂窝网络连接配置，请联系技术人员检查存储空间、文件系统和权限。";
    }
    if (code === "CELLULAR_ACTIVATION_FAILED") {
      return "系统未能启用蜂窝网络连接，请联系技术人员检查联网服务和连接配置。";
    }
    if (CELLULAR_SIM_ERRORS.has(code)) {
      return code === "CELLULAR_SIM_ABSENT"
        ? "断电后确认物联网卡已经正确插入；重新开机后设备会自动重试。"
        : "物联网卡已锁定，请联系物联网卡服务方解锁或更换可用卡。";
    }
    if (CELLULAR_ADDRESS_ERRORS.has(code)) {
      return "蜂窝网络尚未取得可用地址，请检查物联网卡套餐、现场信号和通信模块，设备会自动重试。";
    }
    if (CELLULAR_ROUTE_ERRORS.has(code)) {
      return "设备的网络接口或联网路径异常，请联系技术人员检查网络配置。";
    }
    if (CELLULAR_REACHABILITY_ERRORS.has(code)) {
      return "蜂窝网络已经建立，但后台服务暂时不可达；请保持设备通电，系统会自动重试。";
    }
    if (code === "CELLULAR_EGRESS_GATE_FAILED" || code === "SEALED_FACT_INVALID") {
      return "联网安全检查未通过，设备已停止继续联网。请勿绕过检查，并联系技术人员处理。";
    }
    if (code === "FACTORY_TEST_GATE_CLOSED") {
      return "请先完成本机硬件检查；通过后设备会自动继续联网。";
    }
    if (code === "SYSTEM_FACTS_INVALID") {
      return "设备无法读取自身基础状态，请展开诊断信息并联系技术人员处理。";
    }
    if (TIME_SYNC_ERRORS.has(code)) {
      return code === "TIME_SYNC_PENDING" || code === "TIME_NOT_TRUSTED"
        ? "蜂窝网络已经建立，系统正在校准时间，请保持设备通电并等待自动完成。"
        : "系统时间校准没有完成，请保持设备联网；若持续不恢复，请联系技术人员检查校时服务。";
    }
    if (code === "STATUS_UNAVAILABLE") {
      return "保持热点连接并刷新；若持续无法读取状态，请联系技术人员检查蜂窝联网服务。";
    }
    return "蜂窝联网尚未完成，请检查通信模块、物联网卡和现场信号，设备会自动重试。";
  }
  if (node.id === "ENROLLMENT_AND_CREDENTIALS") return "先确认蜂窝网络和系统时间正常；如需协助，请展开诊断信息并提供技术问题代码。";
  if (node.id === "RUNTIME_AND_MQTT") return "检查设备日常服务、控制板接线和云端连接；状态长时间不更新时，先确认设备服务是否仍在运行。";
  if (node.id === "CLOUD_EVIDENCE") return "检查摄像头、验收照片上传和本地存储；如需协助，请在后台设备详情继续定位。";
  if (node.id === "CLOUD_DECISION_AND_AUTHORIZATION") return "请到后台设备详情查看本次设备功能检查结果和对应处理建议。";
  if (node.id === "FACTORY_SEAL") return "不要绕过封存检查；修复设备服务或授权问题后，页面会自动恢复。";
  if (code === "STATUS_UNAVAILABLE") return "保持热点连接并刷新；若持续无状态，请检查对应设备服务。";
  return "按页面说明排除对应硬件或服务问题后再继续；如需协助，请展开下方诊断信息。";
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
  setTextIfChanged(byId("current-index"), `当前步骤 ${nodeMeta.number}`);
  setTextIfChanged(byId("current-title"), nodeMeta.title);
  setTextIfChanged(byId("current-state"), currentStateMeta.label);
  byId("current-state").className = `state-badge ${currentStateMeta.className}`;
  const currentDescription = describeDetail(currentFlowNode);
  setTextIfChanged(byId("current-detail"), currentDescription);

  const blockedStepError = blockedStepErrorCode(currentFlowNode);
  const recoveryProblem = factoryRecoveryProblem(currentFlowNode);
  const issueVisible = ["BLOCKED", "UNKNOWN"].includes(currentFlowNode.state)
    || (currentFlowNode.errorCode && currentFlowNode.errorCode !== "NONE")
    || blockedStepError
    || recoveryProblem;
  byId("issue-panel").hidden = !issueVisible;
  let problem = null;
  if (issueVisible) {
    problem = describeError(currentFlowNode);
    setTextIfChanged(byId("issue-title"), problem.description);
    setTextIfChanged(
      byId("issue-action"),
      recoveryInstruction(currentFlowNode, problem.code),
    );
    setTextIfChanged(byId("issue-code"), "如需报修，请展开下方诊断信息并提供技术问题代码。");
  }
  setTextIfChanged(byId("flow-error-code"), technicalCodeText(problem?.code));

  const announcementSignature = [
    currentFlowNode.id,
    currentFlowNode.state,
    currentFlowNode.detailCode,
    currentFlowNode.errorCode,
    problem?.code,
  ].join("|");
  if (announcementSignature !== lastProgressAnnouncement) {
    lastProgressAnnouncement = announcementSignature;
    const issueAnnouncement = problem
      ? `问题：${problem.description}。请按页面提示处理。`
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
    item.textContent = `${STEP_LABELS[step.id] || "其他检查项目"} · ${stateMeta(step.state).label}`;
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
    ? factoryPhaseLabel(factory.phase)
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
    const context = mappedLabel(
      RECOVERY_CONTEXT_LABELS,
      recovery.context,
      "当前硬件操作",
    );
    const reason = mappedErrorDescription(recovery.resultCode)
      || "未能完成安全恢复，需要技术人员确认";
    byId("recovery-reason").textContent = `${context}：${reason}`;
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
  byId("stage").textContent = firstBootStageLabel(status.stage);
  byId("release").textContent = releaseLabel(status.image?.releaseId);
  byId("test-status").textContent = factoryTestStatusLabel(status.factoryTest?.status);
  byId("time-trusted").textContent = status.system?.timeTrusted === true ? "已同步" : "未同步";
  byId("seal-status").textContent = factorySealStatusLabel(status.factorySeal?.statusCode);
  byId("last-error").textContent = errorDescription(status.lastErrorCode || "NONE");
  byId("last-error-code").textContent = technicalCodeText(status.lastErrorCode);
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
    const message = actionErrorDescription(error.message);
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
    byId("action-result").textContent = "请先选择控制板远程升级线路的实际装配情况。";
    return;
  }
  const actionContext = Object.freeze({
    cleanRecovery: operation === "RECOVER"
      && currentStatus?.factoryTest?.recovery?.context === "CLEAN",
  });
  let prompt = typeof definition.prompt === "function"
    ? definition.prompt(actionContext)
    : definition.prompt;
  if (operation === "FINALIZE" && currentStatus.factoryTest.mcuPeripheralEvidenceMode === "SIMULATED_PERIPHERALS") {
    prompt = "当前报告会记录本次检查所用的硬件来源。确认生成本机硬件检查报告并继续后续接入流程？";
  }
  if (!window.confirm(prompt)) return;
  await postAction(operation, definition.parameters(actionContext));
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
  byId("seal-result").textContent = "正在核对本次验收授权并保存封存结果……";
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
    const message = actionErrorDescription(error.message);
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

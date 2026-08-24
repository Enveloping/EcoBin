package org.enveloping.ecobin.operations.application.governance;

import org.enveloping.ecobin.operations.web.v1.OperationsModels.ReliableTaskTypeView;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;

/** Human-readable metadata for reliable task types shown to operators. */
final class ReliableTaskTypeCatalog {

    private static final List<Definition> DEFINITIONS = List.of(
            device("REQUEST_DEVICE_ACCEPTANCE", "采集设备验收证据",
                    "平台在机器验收阶段向香橙派发起挑战，等待设备回传联网、自检、摄像头等可信证据；未完成时会阻止设备通过验收。"),
            device("AUTHORIZE_FACTORY_SEAL", "下发厂家封存授权",
                    "设备验收通过后，平台按当前验收代次、证据和预装袋快照向香橙派下发封存授权；失败会阻止首次激活继续收敛。"),
            device("ENSURE_DEVICE_CONFIGURATION", "收敛设备配置",
                    "平台把目标配置版本下发到香橙派；当前 fixed-frame（固定帧）设备只要求香橙派可靠保存并设为活动配置，即可把配置状态投影为 APPLIED。兼容字段 mcuCommandUid 不代表 MCU 已收到或应用配置。"),
            device("MEASURE_EMPTY_BAG_BASELINE", "测量空袋重量基线",
                    "平台要求设备称量当前空袋皮重，香橙派计算并上报稳定结果；缺少有效基线时，依赖重量判断的投递会被阻止。"),
            device("SYNC_DEVICE_ENTRY_URL", "同步设备公开入口",
                    "平台把固定的小程序跳转地址和设备公开码同步到香橙派，香橙派保存后转发给 MCU 用于显示设备二维码。"),
            device("OPEN_REMOTE_SUPPORT_TUNNEL", "开启远程支持隧道",
                    "运维人员获准远程调试时，平台要求香橙派建立有时限的反向 SSH 隧道；它只开放维护通道，不会改变投递或清运数据。"),
            device("CLOSE_REMOTE_SUPPORT_TUNNEL", "关闭远程支持隧道",
                    "远程支持结束、到期或被撤销时，平台要求香橙派关闭反向 SSH 隧道，避免维护入口继续开放。"),
            device("START_MCU_FIRMWARE_UPDATE", "启动 MCU 固件升级",
                    "平台要求香橙派下载并校验指定固件，再驱动 MCU 升级并回传进度；失败不会被当成升级成功。"),
            device("SAMPLE_FULLNESS", "采集设备满溢度",
                    "历史主动采样任务。V25 起后端不再创建或下发该任务，当前满溢状态由设备针对当前袋被动上报；控制台看到它通常是历史数据，恢复它不能刷新当前满溢状态。"),
            device("PROVIDE_PHOTO_UPLOAD_GRANT", "下发照片上传授权",
                    "投递或清运需要拍照时，平台向香橙派提供有时限的 COS 上传授权；设备随后直传照片并回传 URL。"),
            device("CONFIRM_EDGE_EVENT", "确认边缘事件已接收",
                    "后端已经可靠保存设备事件后向香橙派回送确认，设备据此清理本地待上报事件；它不会重复创建业务订单。"),
            device("START_DELIVERY_SESSION", "启动投递会话",
                    "用户扫码并选择投口后，后端要求设备启动一次有唯一身份的投递流程；设备完成上报后才会创建投递订单。"),
            device("START_CLEAN_OPERATION", "启动清运操作",
                    "清运员扫码并确认袋码后，后端要求设备执行开门、换袋和称重流程；完成事件会推动清运记录和新袋基线。"),
            new Definition("PROCESS_INBOX", "处理可靠入站消息",
                    "OneNet 或资金渠道消息进入平台后，由该任务按消息唯一身份处理；重复消息会复用原处理结果，不应重复产生业务副作用。",
                    List.of("DEVICE", "FUNDS"),
                    List.of("INBOX_PROCESSING")),
            funds("CREATE_NATIVE_PAYMENT", "创建充值二维码订单",
                    "用户发起充值后，平台调用微信支付创建 Native 支付单并保存渠道结果；失败时充值不会被视为已支付。"),
            funds("QUERY_NATIVE_PAYMENT", "查询充值支付状态",
                    "回调缺失或状态未收敛时，平台主动查询微信支付，以渠道事实更新充值单状态。"),
            funds("CLOSE_NATIVE_PAYMENT", "关闭未支付充值单",
                    "充值超时或用户放弃后，平台调用微信关闭仍未支付的订单，避免旧二维码继续被付款。"),
            funds("POST_RECHARGE_NET_AMOUNT", "入账充值净额",
                    "微信支付证据核对通过后，平台把扣除渠道费用后的实际金额记入机构可用资金；未完成时余额不会增加。"),
            funds("CREATE_MERCHANT_TRANSFER_AUTHORIZATION", "创建微信收款授权",
                    "用户申请开通商家转账收款时，平台向微信创建授权申请，随后小程序才能调起用户确认页面。"),
            funds("QUERY_MERCHANT_TRANSFER_AUTHORIZATION", "查询微信收款授权",
                    "用户完成或离开授权页面后，平台查询微信的最终授权状态；只有当前授权为 ACTIVE，后续新的手动提现和自动提现才允许创建，并在实际提交前再次复核。"),
            funds("SUBMIT_MERCHANT_TRANSFER", "提交微信提现",
                    "提现审核通过后，平台按原提现单和 OpenID 调用微信商家转账；任务完成不等同于用户一定已收款，仍以渠道终态为准。"),
            funds("QUERY_MERCHANT_TRANSFER", "查询微信提现状态",
                    "转账提交后状态不明确或回调缺失时，平台向微信查询原转账单，依据渠道证据把提现收敛到成功、失败或继续处理中。"),
            funds("CANCEL_MERCHANT_TRANSFER", "核对历史撤销转账任务",
                    "兼容历史数据的任务类型；当前只查询原微信转账状态，不会再次向微信发起撤销，避免产生新的资金副作用。"),
            new Definition("AUTO_REVIEW_DELIVERY_ORDER", "自动审核投递订单",
                    "系统依据订单创建时固化的审核模式、金额上限和到期时间快照，并结合订单异常或阻断事实执行自动审核；机构后来修改规则不会追溯改变旧订单。审核通过后才会入账返现，并可能触发本次自动提现。",
                    List.of("RECYCLING"), List.of("TIMER")));

    private ReliableTaskTypeCatalog() { }

    static List<ReliableTaskTypeView> entries(
            List<Observation> observations) {
        Map<String, MutableEntry> entries = new LinkedHashMap<>();
        for (Definition definition : DEFINITIONS) {
            entries.put(definition.taskType(), new MutableEntry(definition));
        }
        for (Observation observation : observations) {
            MutableEntry entry = entries.computeIfAbsent(
                    observation.taskType(), ReliableTaskTypeCatalog::unknown);
            entry.executionLanes.add(observation.executionLane());
            entry.taskKinds.add(observation.taskKind());
        }
        return entries.values().stream()
                .map(MutableEntry::view)
                .sorted(Comparator
                        .comparingInt(ReliableTaskTypeCatalog::laneOrder)
                        .thenComparing(ReliableTaskTypeView::displayName)
                        .thenComparing(ReliableTaskTypeView::taskType))
                .toList();
    }

    private static Definition device(
            String taskType, String displayName, String description) {
        return new Definition(taskType, displayName, description,
                List.of("DEVICE"), List.of("BUSINESS_INTENT"));
    }

    private static Definition funds(
            String taskType, String displayName, String description) {
        return new Definition(taskType, displayName, description,
                List.of("FUNDS"), List.of("BUSINESS_INTENT"));
    }

    private static MutableEntry unknown(String taskType) {
        return new MutableEntry(new Definition(
                taskType,
                "其他系统任务",
                "数据库中已经存在该任务类型，但当前版本尚未登记专门的中文业务说明。请结合执行通道、目标和阻断诊断判断，不要仅凭内部代码执行恢复。",
                List.of(),
                List.of()));
    }

    private static int laneOrder(ReliableTaskTypeView view) {
        if (view.executionLanes().contains("DEVICE")) return 0;
        if (view.executionLanes().contains("RECYCLING")) return 1;
        if (view.executionLanes().contains("FUNDS")) return 2;
        return 3;
    }

    record Observation(
            String taskType, String executionLane, String taskKind) { }

    private record Definition(
            String taskType,
            String displayName,
            String description,
            List<String> executionLanes,
            List<String> taskKinds) { }

    private static final class MutableEntry {
        private final Definition definition;
        private final LinkedHashSet<String> executionLanes;
        private final LinkedHashSet<String> taskKinds;

        private MutableEntry(Definition definition) {
            this.definition = definition;
            this.executionLanes = new LinkedHashSet<>(
                    definition.executionLanes());
            this.taskKinds = new LinkedHashSet<>(definition.taskKinds());
        }

        private ReliableTaskTypeView view() {
            return new ReliableTaskTypeView(
                    definition.taskType(),
                    definition.displayName(),
                    definition.description(),
                    new ArrayList<>(executionLanes),
                    new ArrayList<>(taskKinds));
        }
    }
}

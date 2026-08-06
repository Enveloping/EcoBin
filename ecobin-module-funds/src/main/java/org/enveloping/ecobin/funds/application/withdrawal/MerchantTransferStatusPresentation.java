package org.enveloping.ecobin.funds.application.withdrawal;

/**
 * Converts WeChat's machine error into a safe explanation suitable for both
 * the mini-program user and an operator. The raw response body is deliberately
 * never exposed through the business API.
 */
record MerchantTransferStatusPresentation(
        String errorCode,
        String message) {

    static MerchantTransferStatusPresentation from(String errorCode) {
        if (errorCode == null || errorCode.isBlank()) {
            return new MerchantTransferStatusPresentation(null, null);
        }
        String code = errorCode.trim();
        String message = switch (code) {
            case "NOT_ENOUGH" ->
                    "微信返回出资商户余额不足；提现资金仍保持冻结，系统会按出款闸门和原商户单号继续处理";
            case "NO_AUTH" ->
                    "微信商家转账权限不可用，系统已停止自动提交，请平台管理员检查商户产品权限";
            case "SIGN_ERROR", "SIGNATURE_ERROR",
                    "RESPONSE_SIGNATURE_INVALID" ->
                    "微信支付签名或响应验签失败，系统已停止自动归并，请平台管理员检查商户密钥与微信支付公钥";
            case "PARAM_ERROR" ->
                    "微信拒绝了转账参数，系统已停止自动提交，请平台管理员根据提现单号核查请求配置";
            case "INVALID_REQUEST" ->
                    "微信拒绝当前转账请求，请平台管理员核查商户、转账场景和小程序 AppID 绑定";
            case "NOT_FOUND" ->
                    "微信暂未查询到原转账单，系统会继续使用原商户单号恢复处理";
            case "SYSTEM_ERROR", "NETWORK_ERROR", "CLIENT_INTERRUPTED",
                    "FREQUENCY_LIMIT_EXCEED", "RATELIMIT_EXCEEDED",
                    "FREQUENCY_LIMIT", "FREQUENCY_LIMITED" ->
                    "微信接口暂时不可用或结果不确定，系统会使用原商户单号查单并按退避策略重试";
            case "ALREADY_EXISTS" ->
                    "微信提示商户单号已存在，系统会先查询原单，不会创建新的提现单号";
            default -> "微信渠道返回未识别错误 " + code
                    + "，结果尚不明确；系统会使用原商户单号查单并按退避策略处理，请平台管理员持续关注";
        };
        return new MerchantTransferStatusPresentation(code, message);
    }
}

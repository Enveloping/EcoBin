package org.enveloping.ecobin.framework.wechat;

import lombok.Data;

/**
 * 微信 code2session 接口响应
 */
@Data
public class WechatSessionResponse {

    /** 用户唯一标识 */
    private String openid;

    /** 会话密钥 */
    private String sessionKey;

    /** 用户在开放平台的唯一标识（需满足条件才有值） */
    private String unionid;

    /** 错误码（0 表示成功） */
    private Integer errcode;

    /** 错误信息 */
    private String errmsg;

    public boolean isSuccess() {
        return errcode == null || errcode == 0;
    }
}

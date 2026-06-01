package org.enveloping.ecobin.common.enums;

import lombok.Getter;

/**
 * 登录方式
 */
@Getter
public enum LoginType {

    UNKNOWN(0, "未知"),
    PHONE(1, "手机号"),
    IC_CARD(2, "IC卡"),
    FACE(3, "人脸识别"),
    QR_CODE(4, "二维码");

    private final int code;
    private final String desc;

    LoginType(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }
}

package org.enveloping.ecobin.common.enums;

import lombok.Getter;

/**
 * 审核状态
 */
@Getter
public enum AuditStatus {

    PENDING(0, "待审核"),
    APPROVED(1, "审核通过"),
    REJECTED(2, "审核拒绝");

    private final int code;
    private final String desc;

    AuditStatus(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }
}

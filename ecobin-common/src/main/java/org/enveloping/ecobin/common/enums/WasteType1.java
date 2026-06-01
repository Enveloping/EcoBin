package org.enveloping.ecobin.common.enums;

import lombok.Getter;

/**
 * 垃圾一级分类
 */
@Getter
public enum WasteType1 {

    NONE(0, "无"),
    KITCHEN(1, "厨余垃圾"),
    RECYCLABLE(2, "可回收垃圾"),
    HAZARDOUS(3, "有害垃圾"),
    OTHER(4, "其他垃圾");

    private final int code;
    private final String desc;

    WasteType1(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }
}

package org.enveloping.ecobin.common.enums;

import lombok.Getter;

/**
 * 垃圾二级分类
 */
@Getter
public enum WasteType2 {

    NONE(0, "不区分"),
    PAPER(1, "纸类"),
    PLASTIC(2, "塑料"),
    FABRIC(3, "织物"),
    METAL(4, "金属"),
    OTHER(5, "其他");

    private final int code;
    private final String desc;

    WasteType2(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }
}

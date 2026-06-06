package org.enveloping.ecobin.business.dto;

/**
 * 开投口结果：返回新建的投递记录ID与投递标识符。
 * 投递标识符需由设备在关投口上报时原样带回，用于关联回填同一条记录。
 */
public record OpenDoorResult(Long orderId, String deliveryToken) {
}

package org.enveloping.ecobin.device.service;

/**
 * 设备下行指令服务。
 * <p>
 * 当前项目尚无设备下行通道（MQTT/长连接），实现为占位：仅记录指令意图，不阻塞业务主流程。
 * 后续对接 IoT 网关后在此补充真实下发逻辑。
 */
public interface DeviceCommandService {

    /**
     * 下发「开投口」指令，并携带本次投递标识符。
     *
     * @param deviceSn      设备序列号
     * @param doorIndex     投口号
     * @param deliveryToken 投递标识符（设备关投口上报时需原样带回）
     */
    void sendOpenDoor(String deviceSn, Integer doorIndex, String deliveryToken);

    /**
     * 下发「开清运门」指令，并携带本次清运订单ID。
     *
     * @param deviceSn     设备序列号
     * @param doorIndex    投口号（物理控制，开哪个投口）
     * @param cleanOrderId 清运订单ID（设备记住，毛重/去皮/图片上报时原样带回）
     */
    void sendOpenCleanDoor(String deviceSn, Integer doorIndex, Long cleanOrderId);
}

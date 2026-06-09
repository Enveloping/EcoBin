package org.enveloping.ecobin.business.service;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.business.entity.CleanBag;

import java.math.BigDecimal;

/**
 * 垃圾袋追踪服务：维护每个设备投口"当前那只袋"的去皮重量与编号。
 */
public interface CleanBagService extends IService<CleanBag> {

    /** 查询某设备投口当前的垃圾袋记录（无则返回 null） */
    CleanBag getCurrent(Long deviceId, Integer doorIndex);

    /**
     * 换袋上报：upsert 某设备投口的当前垃圾袋编号与去皮重量。
     *
     * @param tenantId   租户ID
     * @param deviceId   设备ID
     * @param doorIndex  投口号
     * @param bagQr      新垃圾袋编号
     * @param tareWeight 新垃圾袋去皮重量
     * @param userId     换袋清运人ID
     */
    void replaceBag(Long tenantId, Long deviceId, Integer doorIndex, String bagQr, BigDecimal tareWeight, Long userId);
}

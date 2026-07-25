package org.enveloping.ecobin.recycling.application.legacy;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.recycling.domain.legacy.CleanBag;
import org.enveloping.ecobin.recycling.infrastructure.persistence.mapper.CleanBagMapper;
import org.enveloping.ecobin.common.constant.Constants;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;

@Service
@RequiredArgsConstructor
public class CleanBagServiceImpl extends ServiceImpl<CleanBagMapper, CleanBag> implements CleanBagService {

    @Override
    public CleanBag getCurrent(Long deviceId, Integer doorIndex) {
        return lambdaQuery()
                .eq(CleanBag::getDeviceId, deviceId)
                .eq(CleanBag::getDoorIndex, doorIndex)
                .one();
    }

    @Override
    public void replaceBag(Long tenantId, Long deviceId, Integer doorIndex, String bagQr, BigDecimal tareWeight, Long userId) {
        CleanBag existing = getCurrent(deviceId, doorIndex);
        if (existing == null) {
            CleanBag bag = new CleanBag();
            bag.setTenantId(tenantId != null ? tenantId : Constants.DEFAULT_TENANT_ID);
            bag.setDeviceId(deviceId);
            bag.setDoorIndex(doorIndex);
            bag.setBagQr(bagQr);
            bag.setTareWeight(tareWeight);
            bag.setUserId(userId);
            save(bag);
        } else {
            CleanBag update = new CleanBag();
            update.setId(existing.getId());
            update.setBagQr(bagQr);
            update.setTareWeight(tareWeight);
            update.setUserId(userId);
            updateById(update);
        }
    }
}

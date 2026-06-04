package org.enveloping.ecobin;

import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.service.DeviceService;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 验证 MyBatis-Plus 租户行级隔离：自动注入 tenant_id 过滤、INSERT 自动回填、平台域放行。
 */
@SpringBootTest
@ActiveProfiles("test")
class TenantIsolationTest {

    @Autowired
    private DeviceService deviceService;

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
    }

    @Test
    void tenantScopedQueryAndInsertAreIsolated() {
        // 租户 2 创建一台设备（tenant_id 由拦截器按上下文自动回填）
        TenantContextHolder.setTenantId(2L);
        TenantContextHolder.setIgnore(false);
        deviceService.save(newDevice("SN-T2-001", "租户2设备"));

        // 租户 3 创建一台设备
        TenantContextHolder.setTenantId(3L);
        TenantContextHolder.setIgnore(false);
        deviceService.save(newDevice("SN-T3-001", "租户3设备"));

        // 租户 2 视角：只能看到自己的设备
        TenantContextHolder.setTenantId(2L);
        TenantContextHolder.setIgnore(false);
        List<Device> t2 = deviceService.list();
        assertEquals(1, t2.size(), "租户2应只看到自己的设备");
        assertEquals(2L, t2.get(0).getTenantId());
        assertEquals("SN-T2-001", t2.get(0).getSn());

        // 平台域放行：看到全部
        TenantContextHolder.setTenantId(1L);
        TenantContextHolder.setIgnore(true);
        List<Device> all = deviceService.list();
        assertTrue(all.size() >= 2, "平台域应看到全部设备");
    }

    private Device newDevice(String sn, String name) {
        Device d = new Device();
        d.setSn(sn);
        d.setName(name);
        d.setType(1);
        d.setStatus(1);
        return d;
    }
}

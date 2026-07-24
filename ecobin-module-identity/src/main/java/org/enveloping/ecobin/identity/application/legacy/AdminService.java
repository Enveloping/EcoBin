package org.enveloping.ecobin.identity.application.legacy;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.identity.infrastructure.persistence.entity.Admin;

/**
 * 平台管理员服务接口
 */
public interface AdminService extends IService<Admin> {

    /** 按用户名查询 */
    Admin getByUsername(String username);
}

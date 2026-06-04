package org.enveloping.ecobin.system.service;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.system.entity.Admin;

/**
 * 平台管理员服务接口
 */
public interface AdminService extends IService<Admin> {

    /** 按用户名查询 */
    Admin getByUsername(String username);
}

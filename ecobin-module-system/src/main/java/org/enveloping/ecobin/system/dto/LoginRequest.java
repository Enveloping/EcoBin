package org.enveloping.ecobin.system.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

/**
 * 登录请求
 */
@Data
public class LoginRequest {

    /** 登录主体类型：admin（平台管理员）/ tenant（租户）。缺省按 admin 处理 */
    private String userType;

    @NotBlank(message = "用户名不能为空")
    private String username;

    @NotBlank(message = "密码不能为空")
    private String password;
}

package org.enveloping.ecobin.system.dto;

import jakarta.validation.constraints.NotNull;
import lombok.Data;

/**
 * 租户提升/降低终端用户角色请求。
 * <p>
 * role 仅允许终端角色 1-普通用户 / 2-清运员 / 3-设备管理员，服务层强校验，杜绝写入平台/租户角色（7/8/9）。
 */
@Data
public class UserRoleUpdateRequest {

    /** 目标角色：1-普通用户 2-清运员 3-设备管理员 */
    @NotNull(message = "角色不能为空")
    private Integer role;
}

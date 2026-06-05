package org.enveloping.ecobin.system.dto;

import lombok.Data;
import org.enveloping.ecobin.system.entity.User;

/**
 * 终端用户个人信息视图对象（脱敏）。
 * <p>
 * 仅暴露小程序展示所需字段，剔除 {@code openid/unionid/password} 等敏感信息。
 */
@Data
public class UserProfileVO {

    /** 用户ID */
    private Long id;

    /** 真实姓名 */
    private String realName;

    /** 手机号 */
    private String phone;

    /** 微信昵称 */
    private String nickname;

    /** 微信头像 URL */
    private String avatar;

    /** 角色：3-设备管理员 2-清运员 1-普通用户 */
    private Integer role;

    /** 状态：0-禁用 1-启用 */
    private Integer status;

    public static UserProfileVO from(User user) {
        UserProfileVO vo = new UserProfileVO();
        vo.setId(user.getId());
        vo.setRealName(user.getRealName());
        vo.setPhone(user.getPhone());
        vo.setNickname(user.getNickname());
        vo.setAvatar(user.getAvatar());
        vo.setRole(user.getRole());
        vo.setStatus(user.getStatus());
        return vo;
    }
}

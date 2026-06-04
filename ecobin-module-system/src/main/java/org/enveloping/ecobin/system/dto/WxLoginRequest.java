package org.enveloping.ecobin.system.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

/**
 * 微信小程序登录请求
 */
@Data
public class WxLoginRequest {

    /** 微信小程序 wx.login() 返回的临时 code */
    @NotBlank(message = "微信登录 code 不能为空")
    private String code;

    /** 所属租户的小程序 AppID（用于定位租户与 secret） */
    @NotBlank(message = "小程序 appid 不能为空")
    private String appid;
}

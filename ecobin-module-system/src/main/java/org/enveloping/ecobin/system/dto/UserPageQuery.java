package org.enveloping.ecobin.system.dto;

import lombok.Data;

/**
 * 用户分页查询参数
 */
@Data
public class UserPageQuery {

    /** 页码（从1开始） */
    private Integer page = 1;

    /** 每页大小 */
    private Integer pageSize = 20;
}

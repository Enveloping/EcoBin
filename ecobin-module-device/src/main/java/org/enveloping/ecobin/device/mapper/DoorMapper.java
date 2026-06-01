package org.enveloping.ecobin.device.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.enveloping.ecobin.device.entity.Door;

import java.util.List;

@Mapper
public interface DoorMapper extends BaseMapper<Door> {

    /** 按设备ID查询投口列表 */
    @Select("SELECT * FROM biz_door WHERE device_id = #{deviceId} ORDER BY sort_order")
    List<Door> selectByDeviceId(@Param("deviceId") Long deviceId);
}

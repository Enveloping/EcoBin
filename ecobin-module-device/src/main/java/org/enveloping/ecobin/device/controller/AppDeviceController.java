package org.enveloping.ecobin.device.controller;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.common.result.Result;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.entity.Door;
import org.enveloping.ecobin.device.service.DeviceService;
import org.enveloping.ecobin.device.service.DoorService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 小程序终端用户 C 端接口：设备与投口（只读）。
 * <p>
 * 终端用户可查看本租户下的设备及投口信息，用于投递前选择目标投口。
 * 数据隔离：租户拦截器自动注入 {@code tenant_id}，仅返回本租户数据。
 */
@RestController
@RequestMapping("/api/app/device")
@RequiredArgsConstructor
public class AppDeviceController {

    private final DeviceService deviceService;
    private final DoorService doorService;

    /** 设备分页列表（仅本租户） */
    @GetMapping
    public Result<PageResult<Device>> page(@RequestParam(defaultValue = "1") int page,
                                           @RequestParam(defaultValue = "20") int pageSize) {
        return Result.ok(deviceService.pageDevices(page, pageSize));
    }

    /** 设备详情 */
    @GetMapping("/{id}")
    public Result<Device> get(@PathVariable Long id) {
        return Result.ok(deviceService.getById(id));
    }

    /** 某设备的投口列表 */
    @GetMapping("/{deviceId}/doors")
    public Result<List<Door>> doors(@PathVariable Long deviceId) {
        return Result.ok(doorService.listByDeviceId(deviceId));
    }
}

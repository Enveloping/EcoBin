package org.enveloping.ecobin.device.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.common.result.Result;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.service.DeviceService;
import org.springframework.web.bind.annotation.*;

/**
 * 设备管理接口
 */
@RestController
@RequestMapping("/api/device")
@RequiredArgsConstructor
public class DeviceController {

    private final DeviceService deviceService;

    @GetMapping
    public Result<PageResult<Device>> page(@RequestParam(defaultValue = "1") int page,
                                            @RequestParam(defaultValue = "20") int pageSize) {
        return Result.ok(deviceService.pageDevices(page, pageSize));
    }

    @GetMapping("/{id}")
    public Result<Device> get(@PathVariable Long id) {
        return Result.ok(deviceService.getById(id));
    }

    @PostMapping
    public Result<Device> create(@Valid @RequestBody Device device) {
        deviceService.save(device);
        return Result.ok(device);
    }

    @PutMapping("/{id}")
    public Result<Device> update(@PathVariable Long id, @Valid @RequestBody Device device) {
        device.setId(id);
        deviceService.updateById(device);
        return Result.ok(deviceService.getById(id));
    }

    @DeleteMapping("/{id}")
    public Result<Void> delete(@PathVariable Long id) {
        deviceService.removeById(id);
        return Result.ok();
    }
}

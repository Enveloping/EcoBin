package org.enveloping.ecobin.device.controller;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.result.Result;
import org.enveloping.ecobin.device.entity.Door;
import org.enveloping.ecobin.device.service.DoorService;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 投口管理接口
 */
@RestController
@RequestMapping("/api/device/door")
@RequiredArgsConstructor
public class DoorController {

    private final DoorService doorService;

    @GetMapping("/list/{deviceId}")
    public Result<List<Door>> listByDevice(@PathVariable Long deviceId) {
        return Result.ok(doorService.listByDeviceId(deviceId));
    }

    @GetMapping("/{id}")
    public Result<Door> get(@PathVariable Long id) {
        return Result.ok(doorService.getById(id));
    }

    @PostMapping
    public Result<Door> create(@RequestBody Door door) {
        doorService.save(door);
        return Result.ok(door);
    }

    @PutMapping("/{id}")
    public Result<Door> update(@PathVariable Long id, @RequestBody Door door) {
        door.setId(id);
        doorService.updateById(door);
        return Result.ok(doorService.getById(id));
    }

    @DeleteMapping("/{id}")
    public Result<Void> delete(@PathVariable Long id) {
        doorService.removeById(id);
        return Result.ok();
    }
}

package org.enveloping.ecobin.device.api.port;

/**
 * 设备命令稳定 JSON 摘要边界。业务模块只提交整数、布尔、文本、数组和对象。
 */
public interface DeviceCommandCanonicalizationPort {

    byte[] payloadSha256(Object payload);

    byte[] canonicalBytes(Object value);

    String hex(byte[] value);
}

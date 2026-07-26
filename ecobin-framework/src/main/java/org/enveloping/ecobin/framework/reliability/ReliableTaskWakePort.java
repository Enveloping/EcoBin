package org.enveloping.ecobin.framework.reliability;

/**
 * 以单调 wakeVersion 唤醒原可靠任务，不创建替代任务。
 */
public interface ReliableTaskWakePort {

    long wake(ReliableTaskWake wake);
}

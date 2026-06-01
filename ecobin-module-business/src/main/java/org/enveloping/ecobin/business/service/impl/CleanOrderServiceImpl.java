package org.enveloping.ecobin.business.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import org.enveloping.ecobin.business.entity.CleanOrder;
import org.enveloping.ecobin.business.mapper.CleanOrderMapper;
import org.enveloping.ecobin.business.service.CleanOrderService;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.springframework.stereotype.Service;

import java.util.UUID;

@Service
public class CleanOrderServiceImpl extends ServiceImpl<CleanOrderMapper, CleanOrder> implements CleanOrderService {

    @Override
    public boolean save(CleanOrder order) {
        if (order.getTenantId() == null) {
            order.setTenantId(Constants.DEFAULT_TENANT_ID);
        }
        if (order.getOrderSn() == null) {
            order.setOrderSn("C" + System.currentTimeMillis() + UUID.randomUUID().toString().substring(0, 4));
        }
        if (order.getAuditStatus() == null) {
            order.setAuditStatus(0);
        }
        if (order.getStatus() == null) {
            order.setStatus(0);
        }
        return super.save(order);
    }

    @Override
    public PageResult<CleanOrder> pageOrders(int page, int pageSize) {
        Page<CleanOrder> p = new Page<>(page, pageSize);
        Page<CleanOrder> result = page(p, new LambdaQueryWrapper<CleanOrder>().orderByDesc(CleanOrder::getCreateTime));
        return PageResult.of(result.getRecords(), result.getTotal(), page, pageSize);
    }

    @Override
    public CleanOrder audit(Long id, Integer auditStatus) {
        CleanOrder order = getById(id);
        if (order == null) {
            throw new BusinessException(404, "清运订单不存在: id=" + id);
        }
        if (order.getAuditStatus() != 0) {
            throw new BusinessException(400, "该订单已审核，不可重复审核");
        }
        CleanOrder update = new CleanOrder();
        update.setId(id);
        update.setAuditStatus(auditStatus);
        update.setStatus(auditStatus == 1 ? 1 : 2);
        updateById(update);
        return getById(id);
    }
}

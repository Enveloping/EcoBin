package org.enveloping.ecobin.recycling.infrastructure.persistence.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.enveloping.ecobin.recycling.domain.legacy.CleanBag;

@Mapper
public interface CleanBagMapper extends BaseMapper<CleanBag> {
}

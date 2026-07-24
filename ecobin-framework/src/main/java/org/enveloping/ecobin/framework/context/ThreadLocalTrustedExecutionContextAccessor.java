package org.enveloping.ecobin.framework.context;

import org.springframework.stereotype.Component;

@Component
public class ThreadLocalTrustedExecutionContextAccessor implements TrustedExecutionContextPort {

    @Override
    public TrustedExecutionContext current() {
        return TrustedExecutionContextHolder.getRequired();
    }
}

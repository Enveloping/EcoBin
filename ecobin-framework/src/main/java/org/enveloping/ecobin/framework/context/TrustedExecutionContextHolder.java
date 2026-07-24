package org.enveloping.ecobin.framework.context;

public final class TrustedExecutionContextHolder {

    private static final ThreadLocal<TrustedExecutionContext> CONTEXT = new ThreadLocal<>();

    private TrustedExecutionContextHolder() {
    }

    public static void set(TrustedExecutionContext context) {
        CONTEXT.set(context);
    }

    public static TrustedExecutionContext getRequired() {
        TrustedExecutionContext context = CONTEXT.get();
        if (context == null) {
            throw new IllegalStateException("trusted execution context is not available");
        }
        return context;
    }

    public static void clear() {
        CONTEXT.remove();
    }
}

package org.enveloping.ecobin.identity.application.web;

import org.springframework.web.context.request.RequestAttributes;
import org.springframework.web.context.request.RequestContextHolder;

public final class TargetWebAuditRequestContext {

    public static final String ATTRIBUTE =
            TargetWebAuditRequestContext.class.getName() + ".descriptor";

    private TargetWebAuditRequestContext() {
    }

    public static void describe(String actionCode, String targetIdentity) {
        RequestAttributes attributes = RequestContextHolder.getRequestAttributes();
        if (attributes != null) {
            attributes.setAttribute(
                    ATTRIBUTE,
                    new Descriptor(actionCode, targetIdentity),
                    RequestAttributes.SCOPE_REQUEST);
        }
    }

    public record Descriptor(
            String actionCode,
            String targetIdentity) {
    }
}

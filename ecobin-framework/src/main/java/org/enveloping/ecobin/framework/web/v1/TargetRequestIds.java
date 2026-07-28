package org.enveloping.ecobin.framework.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.util.StringUtils;

import java.util.UUID;

public final class TargetRequestIds {

    private static final String ATTRIBUTE =
            TargetRequestIds.class.getName() + ".requestId";

    private TargetRequestIds() {
    }

    public static String resolve(HttpServletRequest request) {
        Object existing = request.getAttribute(ATTRIBUTE);
        if (existing instanceof String value) {
            return value;
        }
        String supplied = request.getHeader("X-Request-ID");
        String value = StringUtils.hasText(supplied)
                ? supplied.substring(0, Math.min(supplied.length(), 128))
                : UUID.randomUUID().toString();
        request.setAttribute(ATTRIBUTE, value);
        return value;
    }
}

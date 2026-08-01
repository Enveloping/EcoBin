package org.enveloping.ecobin.framework.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.util.StringUtils;

import java.util.UUID;
import java.util.regex.Pattern;

public final class TargetRequestIds {

    private static final String ATTRIBUTE =
            TargetRequestIds.class.getName() + ".requestId";
    private static final Pattern SAFE_SUPPLIED_REQUEST_ID =
            Pattern.compile("[A-Za-z0-9][A-Za-z0-9._:-]{0,127}");

    private TargetRequestIds() {
    }

    public static String resolve(HttpServletRequest request) {
        Object existing = request.getAttribute(ATTRIBUTE);
        if (existing instanceof String value) {
            return value;
        }
        String supplied = request.getHeader("X-Request-ID");
        String value = StringUtils.hasText(supplied)
                && SAFE_SUPPLIED_REQUEST_ID.matcher(supplied).matches()
                ? supplied
                : UUID.randomUUID().toString();
        request.setAttribute(ATTRIBUTE, value);
        return value;
    }
}

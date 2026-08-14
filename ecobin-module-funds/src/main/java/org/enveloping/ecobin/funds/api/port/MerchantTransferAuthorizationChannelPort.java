package org.enveloping.ecobin.funds.api.port;

import java.time.Instant;
import java.util.Objects;

/** 微信商家转账免确认收款授权渠道边界。 */
public interface MerchantTransferAuthorizationChannelPort {

    AuthorizationResult create(AuthorizationRequest request);

    AuthorizationResult query(AuthorizationQuery query);

    record AuthorizationRequest(
            String mchid,
            String outAuthorizationNo,
            String appid,
            String openid,
            String sceneId,
            String userDisplayName,
            String userRecvPerception,
            String notifyUrl) {

        public AuthorizationRequest {
            requireText(mchid, "mchid");
            requireText(outAuthorizationNo, "outAuthorizationNo");
            requireText(appid, "appid");
            requireText(openid, "openid");
            requireText(sceneId, "sceneId");
            requireText(userDisplayName, "userDisplayName");
            requireText(notifyUrl, "notifyUrl");
            if (userDisplayName.length() > 32) {
                throw new IllegalArgumentException(
                        "userDisplayName must not exceed 32 characters");
            }
            if (userRecvPerception != null
                    && (userRecvPerception.isBlank()
                    || userRecvPerception.length() > 256)) {
                throw new IllegalArgumentException(
                        "userRecvPerception is invalid");
            }
        }
    }

    record AuthorizationQuery(
            String mchid,
            String outAuthorizationNo,
            String appid,
            String openid,
            String sceneId,
            String userDisplayName,
            String userRecvPerception,
            Instant knownChannelCreatedAt) {

        public AuthorizationQuery {
            requireText(mchid, "mchid");
            requireText(outAuthorizationNo, "outAuthorizationNo");
            requireText(appid, "appid");
            requireText(openid, "openid");
            requireText(sceneId, "sceneId");
            requireText(userDisplayName, "userDisplayName");
        }
    }

    record AuthorizationResult(
            Outcome outcome,
            String channelState,
            String outAuthorizationNo,
            String authorizationId,
            String appid,
            String openid,
            String sceneId,
            String userDisplayName,
            String userRecvPerception,
            String packageInfo,
            String closeReason,
            Instant channelCreatedAt,
            Instant authorizedAt,
            Instant closedAt,
            String errorCode,
            Integer httpStatus,
            String diagnostic,
            Instant observedAt) {

        public AuthorizationResult {
            Objects.requireNonNull(outcome, "outcome");
            Objects.requireNonNull(observedAt, "observedAt");
            if (httpStatus != null
                    && (httpStatus < 100 || httpStatus > 599)) {
                throw new IllegalArgumentException(
                        "httpStatus must be a valid HTTP status");
            }
        }

        public AuthorizationResult(
                Outcome outcome,
                String channelState,
                String outAuthorizationNo,
                String authorizationId,
                String appid,
                String openid,
                String sceneId,
                String userDisplayName,
                String userRecvPerception,
                String packageInfo,
                String closeReason,
                Instant channelCreatedAt,
                Instant authorizedAt,
                Instant closedAt,
                String errorCode,
                String diagnostic,
                Instant observedAt) {
            this(outcome, channelState, outAuthorizationNo,
                    authorizationId, appid, openid, sceneId,
                    userDisplayName, userRecvPerception, packageInfo,
                    closeReason, channelCreatedAt, authorizedAt, closedAt,
                    errorCode, null, diagnostic, observedAt);
        }

        public enum Outcome {
            WAIT_USER_CONFIRM,
            ACTIVE,
            CLOSED,
            NOT_FOUND,
            RETRYABLE_FAILURE,
            PERMANENT_FAILURE,
            UNKNOWN_STATE
        }
    }

    private static void requireText(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " must not be blank");
        }
    }
}

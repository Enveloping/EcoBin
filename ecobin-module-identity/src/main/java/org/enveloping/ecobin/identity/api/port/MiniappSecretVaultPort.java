package org.enveloping.ecobin.identity.api.port;

import java.util.UUID;

/**
 * Versioned external storage for organization mini-program AppSecrets.
 *
 * <p>The returned reference is safe to persist in IAM. Secret contents and
 * provider paths must never be persisted in business tables or audit data.</p>
 */
public interface MiniappSecretVaultPort {

    String store(
            UUID operationUid,
            String secretSha256,
            String appSecret);

    String read(String secretReference);
}

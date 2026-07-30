package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.result.CurrentMiniappWalletIdentity;

public interface MiniappWalletIdentityQueryPort {

    CurrentMiniappWalletIdentity current();
}

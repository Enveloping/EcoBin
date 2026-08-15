package org.enveloping.ecobin.device.application.remote;

import org.junit.jupiter.api.Test;

import java.nio.file.attribute.PosixFilePermission;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class OpenSshMaintenanceCertificateSignerTest {

    @Test
    void productionRootOwnedGroupReadableCaIsAccepted() {
        assertThatCode(() -> OpenSshMaintenanceCertificateSigner
                .requireSafePrivateCaPermissions(Set.of(
                        PosixFilePermission.OWNER_READ,
                        PosixFilePermission.GROUP_READ)))
                .doesNotThrowAnyException();
    }

    @Test
    void writableOrWorldAccessibleCaIsRejected() {
        assertThatThrownBy(() -> OpenSshMaintenanceCertificateSigner
                .requireSafePrivateCaPermissions(Set.of(
                        PosixFilePermission.OWNER_READ,
                        PosixFilePermission.GROUP_READ,
                        PosixFilePermission.GROUP_WRITE)))
                .isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> OpenSshMaintenanceCertificateSigner
                .requireSafePrivateCaPermissions(Set.of(
                        PosixFilePermission.OWNER_READ,
                        PosixFilePermission.OTHERS_READ)))
                .isInstanceOf(IllegalStateException.class);
    }
}

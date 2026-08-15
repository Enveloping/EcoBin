package org.enveloping.ecobin.device.application.enrollment;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "ecobin.remote-support")
public class RemoteSupportBootstrapProperties {

    private boolean enabled;
    private String tunnelHost;
    private int tunnelSshPort = 22;
    private String tunnelUser = "ecobin-tunnel";
    private String tunnelServerHostPublicKey;
    private String jumpUser = "ecobin-jump";
    private String maintenanceCaPublicKey;
    private String signerCaPrivateKeyPath;
    private String leaseDesiredDirectory;
    private String leaseActualDirectory;

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public String getTunnelHost() {
        return tunnelHost;
    }

    public void setTunnelHost(String tunnelHost) {
        this.tunnelHost = tunnelHost;
    }

    public int getTunnelSshPort() {
        return tunnelSshPort;
    }

    public void setTunnelSshPort(int tunnelSshPort) {
        this.tunnelSshPort = tunnelSshPort;
    }

    public String getTunnelUser() {
        return tunnelUser;
    }

    public void setTunnelUser(String tunnelUser) {
        this.tunnelUser = tunnelUser;
    }

    public String getTunnelServerHostPublicKey() {
        return tunnelServerHostPublicKey;
    }

    public void setTunnelServerHostPublicKey(String value) {
        this.tunnelServerHostPublicKey = value;
    }

    public String getJumpUser() {
        return jumpUser;
    }

    public void setJumpUser(String jumpUser) {
        this.jumpUser = jumpUser;
    }

    public String getMaintenanceCaPublicKey() {
        return maintenanceCaPublicKey;
    }

    public void setMaintenanceCaPublicKey(String maintenanceCaPublicKey) {
        this.maintenanceCaPublicKey = maintenanceCaPublicKey;
    }

    public String getSignerCaPrivateKeyPath() {
        return signerCaPrivateKeyPath;
    }

    public void setSignerCaPrivateKeyPath(String value) {
        this.signerCaPrivateKeyPath = value;
    }

    public String getLeaseDesiredDirectory() {
        return leaseDesiredDirectory;
    }

    public void setLeaseDesiredDirectory(String value) {
        this.leaseDesiredDirectory = value;
    }

    public String getLeaseActualDirectory() {
        return leaseActualDirectory;
    }

    public void setLeaseActualDirectory(String value) {
        this.leaseActualDirectory = value;
    }
}

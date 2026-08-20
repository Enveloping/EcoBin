#ifndef __ECOBIN_FIRMWARE_IDENTITY_H
#define __ECOBIN_FIRMWARE_IDENTITY_H

/*
 * Documentation-only development identity.  Do not rename this file to the
 * build input for a release.  Use hardware/mcu_firmware_package.py identity so
 * the header and signed package metadata share one release identity.
 */
#define ECOBIN_MCU_FIRMWARE_VERSION       "0.0.0-dev"
#define ECOBIN_MCU_FIRMWARE_VERSION_CODE  0UL
#define ECOBIN_MCU_FIRMWARE_IDENTITY_BYTES \
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00}

#endif /* __ECOBIN_FIRMWARE_IDENTITY_H */

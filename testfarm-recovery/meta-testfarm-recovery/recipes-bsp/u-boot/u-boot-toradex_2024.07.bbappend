FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
# This layer is included only by the dedicated testfarm kas configuration.
SRC_URI:remove = "file://0001-moducop-boot-sd-recovery-before-environment.patch file://0002-mmc-retry-recovery-sd-cmd8.patch"
SRC_URI:append = " file://0001-testfarm-usb-recovery.patch file://testfarm-recovery.cfg"

do_configure[postfuncs] += "testfarm_check_config"
python testfarm_check_config() {
    from pathlib import Path
    for config in (d.getVar('UBOOT_MACHINE') or '').split():
        values = (Path(d.getVar('B')) / config / '.config').read_text().splitlines()
        for required in ('CONFIG_IMX_HAB=y', 'CONFIG_FIT_SIGNATURE=y',
                         'CONFIG_TDX_SECBOOT_HARDENING=y', 'CONFIG_TDX_FB_PROTECTION=y',
                         'CONFIG_TDX_FB_PROTECTION_ALLOW_CMD=y',
                         'CONFIG_FASTBOOT_BUF_ADDR=0x60000000',
                         'CONFIG_FASTBOOT_BUF_SIZE=0x10000000'):
            if required not in values:
                bb.fatal('Testfarm recovery requires ' + required)
        for forbidden in ('CONFIG_FASTBOOT_FLASH=y', 'CONFIG_FASTBOOT_UUU_SUPPORT=y'):
            if forbidden in values:
                bb.fatal('Testfarm recovery forbids ' + forbidden)
}

# Export a fixed, unambiguous bundle input, after the standard FIT/HAB signing tasks.
do_deploy:append() {
    test "${TDX_IMX_HAB_ENABLE}" = 1 || bbfatal "HAB signing is required"
    test "${UBOOT_SIGN_ENABLE}" = 1 || bbfatal "FIT verification is required"
    test "${FIT_GENERATE_KEYS}" = 0 || bbfatal "Use configured trust anchors"
    set -- ${UBOOT_MACHINE}
    test "$#" = 1 || bbfatal "Expected exactly one recovery bootloader configuration"
    recovery_build="${B}/$1"
    recovery_deploy="${DEPLOYDIR}/testfarm-recovery"
    install -d "$recovery_deploy"
    test -s "$recovery_build/csf-for-spl.bin" || bbfatal "Missing SPL HAB CSF"
    test -s "$recovery_build/csf-for-fit.bin" || bbfatal "Missing U-Boot HAB CSF"
    install -m 0644 "$recovery_build/flash.bin" "$recovery_deploy/imx-boot-recovery"
    install -m 0644 "$recovery_build/${UBOOT_DTB_SIGNED}" "$recovery_deploy/u-boot.dtb"
    install -m 0644 "$recovery_build/.config" "$recovery_deploy/u-boot.config"
    install -m 0644 "${TDX_IMX_HAB_CST_SRK_FUSE}" "$recovery_deploy/srk-fuse.bin"
    install -m 0644 "$recovery_build/csf-for-spl.bin" "$recovery_deploy/csf-spl.bin"
    install -m 0644 "$recovery_build/csf-for-fit.bin" "$recovery_deploy/csf-fit.bin"
}

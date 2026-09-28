Yocto linux project that builds custom Linux images with secure boot and virtualization support.

Currently for the following platforms:
* Ci4Rail ModuCop CPU01: Toradex Verdin IMX8MM based platform
* Ci4Rail ModuCop CPU01Plus: Toradex Verdin IMX8MP based platform

The "-standard" images are the production images, "-devtools" is for development and debugging purposes.

Production builds are only singed in ci pipeline, "staging" builds use dummy signatures.

OTA updates are supported using mender. mender is used for
* rootfs A/B updates
* docker compose application updates
* os-customization updates
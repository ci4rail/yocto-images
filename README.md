# ci.os.lmp

Build definitions and scripts for ci.os.lmp platform.

This repo can host several projects (with different Yocto versions, machines etc.). Each project
has a subdirectory in that repo, currently *mender-on-verdin-tdx-dunfell*.

Yocto builds are performed using kas: https://github.com/siemens/kas


## Running developer builds

Developer builds are executed via dobi. 

Examples:

To build the minimal image in mender-on-verdin-tdx-dunfell

```bash
./dobi.sh mvtd-build-min
```

To run interactive yocto shell in mender-on-verdin-tdx-dunfell

```bash
./dobi.sh mvtd-yocto-shell
```

To build all images:

```bash
./dobi.sh build-all
```


## mender-on-verdin-tdx-dunfell

Project to integrate mender.io layer on top of toradex layers and run it on verdin module.

Current status: Toradex yocto can be built (no mender integration). 
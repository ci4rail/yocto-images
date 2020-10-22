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

Call ```dobi.sh list``` to see a list of all jobs. 


### Yocto download and shared state cache

By default, Yocto downloads will be placed into the *./downloads* folder, in order
to share the downloads between all projects.

By default, Yocto shared state cache will be placed into *\<project\>/sstate-cache* folder.

You can override the defaults in the *.env* file (template is provided: *.env.template*), for example:

Note: YOCTO_DOWNLOAD_DIR must be specified without trailing "/", while YOCTO_SSTATE_CACHE_DIR
must be specified *with* trailing "/"

```
export YOCTO_DOWNLOAD_DIR=/opt/yocto/downloads          # Downloads go into /opt/yocto/downloads
export YOCTO_SSTATE_CACHE_DIR=/opt/yocto/sstate-cache/  # State cache goes into /opt/yocto/sstate-cache/<project>/sstate-cache
```


# Projects
## mender-on-verdin-tdx-dunfell

Project to integrate mender.io layer on top of toradex layers for toradex verdin module.

Current status: Toradex yocto can be built (no mender integration). 
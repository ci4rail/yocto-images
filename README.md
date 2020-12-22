# ci.os.lmp

Build definitions and scripts for ci.os.lmp platform.

This repo can host several projects (with different Yocto versions, machines etc.). Each project
has a subdirectory in that repo, e.g. `mender-on-verdin`.

Yocto builds are performed using kas: https://github.com/siemens/kas


## Running developer builds

Developer builds are executed via dobi. 

Examples:

To build the minimal image for `mender-on-verdin`

```bash
./dobi.sh mender-on-verdin-build-minimal-image
```

To run an interactive yocto shell for `mender-on-verdin`

```bash
./dobi.sh mender-on-verdin-yocto-shell
```

To build all images:

```bash
./dobi.sh build-all
```

Call `./dobi.sh list` to see a list of all jobs. 


### Yocto download and shared state cache

By default, Yocto downloads will be placed into the *./downloads* folder, in order
to share the downloads between all projects.

By default, Yocto shared state cache will be placed into *\<project\>/sstate-cache* folder.

You can set default environment variables in the *default.env* file (template is provided: *default.env.template*), for example:

**Note:**

`YOCTO_DOWNLOAD_DIR must be specified without trailing "/", while
YOCTO_SSTATE_CACHE_DIR must be specified *with* trailing "/"`

```
# Version of dobi to download, if not in $PATH
DOWNLOAD_VERSION_DOBI="0.13.0"

# Set the default docker namespace
DOCKER_NAMESPACE=harbor.ci4rail.com

# Directory for Yocto downloads.
# If left empty: downloads go into project local directory
# otherwise: Specify absolute path without trailing "/"
YOCTO_DOWNLOAD_DIR=/opt/yocto/downloads/

# Directory for Yocto shared state cache.
# If left empty: state cache goes into project local directory
YOCTO_SSTATE_CACHE_DIR=/opt/yocto/sstate-cache/

# Mender artifact development suffix
MENDER_ARTIFACT_DEV_SUFFIX=-dev

# Yocto image name suffix
IMAGE_NAME_SUFFIX=-dev
```

# Projects
## mender-on-verdin

Project to integrate mender.io layer on top of toradex layers for toradex verdin module.

Current status: Toradex yocto can be built with mender integration. 
Before building this you need to enter your your mender specific data into `mender-on-verdin-tdx-dunfell/config/mender.env`.
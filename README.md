# ci.os.lmp

Build definitions and scripts for ci.os.lmp platform.

This repo can host several projects (with different Yocto versions, machines etc.). 

Yocto builds are performed using kas: https://github.com/siemens/kas

# Images 

The following images are currently built by this repo
## verdindev-edgefarm

![Build](https://concourse.ci4rail.com/api/v1/teams/main/pipelines/verdindev-edgefarm-dev/jobs/build-verdindev-edgefarm/badge)

An image for the Ci4Rail edgefarm use case (Yocto with mender, docker and iotedge support)

Target Platform: Toradex Verdin Development Board.

## cpu01-edgefarm

![Build](https://concourse.ci4rail.com/api/v1/teams/main/pipelines/cpu01-edgefarm-dev/jobs/build-cpu01-edgefarm/badge)

An image for the Ci4Rail edgefarm use case (Yocto with mender, docker and iotedge support)

Target Platform: Ci4Rail Moducop CPU01.

## cpu01-bringup

![Build](https://concourse.ci4rail.com/api/v1/teams/main/pipelines/cpu01-bringup-dev/jobs/build-cpu01-bringup/badge)

An image for HW platform tests and bringup. Includes mender support, but no docker and iotedge.
Contains many tools for HW testing

Target Platform: Ci4Rail Moducop CPU01.


# Building
It can be either build locally using dobi or via Concourse CI.


## Running developer builds locally

Developer builds are executed via dobi. 

First, enter your your mender specific data into `yocto/config/mender.env` (using template `yocto/config/mender.env.template`)

Second, enter your specific data in `default.env` (using template `default.env.template`). You can use the default setting, but MENDER_DEVICE_ID must be adapted to the device you want to use to [deploy locally built images](#Deploy images via mender).
```
export MENDER_DEVICE_ID=<device id from mender portal>
```

For example, to build the image for `cpu01-edgefarm`:

```bash
./dobi.sh cpu01-edgefarm-build-image
```

To run an interactive yocto shell for `cpu01-edgefarm`

```bash
./dobi.sh cpu01-edgefarm-yocto-shell
```

To build all images:

```bash
./dobi.sh build-all
```

### Deploy images via mender

Download [Mender CLI](https://docs.mender.io/downloads#mender-cli)

Login with `mender-cli`.

Upload image to mender:
```bash
./dobi.sh cpu01-edgefarm-mender-upload
```

To deploy an image to a specific device:

Set the device ID you want to use for deployment, if you haven't specified it in `default.env`:
```bash
export MENDER_DEVICE_ID=<device id from mender portal>
```
Start deployment
```bash
./dobi.sh cpu01-edgefarm-mender-deploy
```

Call `./dobi.sh list` to see a list of all jobs. 

#### Yocto download and shared state cache

By default, Yocto downloads will be placed into the *./downloads* folder, in order
to share the downloads between all projects.

By default, Yocto shared state cache will be placed into *\yocto/sstate-cache* folder.

You can set default environment variables in the *default.env* file (template is provided: *default.env.template*), for example:

**Note:**

**YOCTO_DOWNLOAD_DIR must be specified without trailing "/", while
YOCTO_SSTATE_CACHE_DIR must be specified *with* trailing "/"**

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

## CI/CD configuration

### General perparations

First download `fly` from your concourse server. The production concourse server that is reachable at https://concourse.ci4rail.com.

```bash
sudo wget https://concourse.ci4rail.com/api/v1/cli?arch=amd64&platform=linux -O /usr/local/bin/fly
sudo chmod +x /usr/local/bin/
```

Then login to your concourse instance.

```bash
fly --target dev login --concourse-url https://concourse.ci4rail.com
```

The following steps are performed inside `yocto` subdirectory:

Copy and adapt `ci/credentials.template.yaml` it to your needs. 

```bash
cp ci/credentials.template.yaml ci/credentials-prod.yaml
```
*Note: `ci/credentials-prod.yaml` and `ci/credentials.yaml` are ignored by git. In this file you can store access credentials and keys that won't be checked in. If you are using some third party vault for credentials that integrates well into concourse, you won't need this file.*


### Activate build pipeline

Example for setting the pipeline (here: to create a pipeline for cpu01-bringup image):
```bash
cd yocto
fly -t dev set-pipeline -c pipeline.yaml -p cpu01-bringup -l ci/config-dev.yaml -l ci/credentials.yaml -v name=cpu01-bringup
```

To set all pipelines, use
```bash
cd yocto
./set-dev-pipelines.sh
```

*Note: Currently, only `ci/config-dev.yaml` exists. It monitors the directory `yocto` on `main` branch for changes and triggeres a build. Under normal circumstances no modifications are needed in this file. 
`ci/config-prod.yaml` will be added later when release images are required.

The pipeline produces:
* A TEZI tar file that can be installed with the tdx-installer. This file is stored on https://minio.ci4rail.com/, for example: https://minio.ci4rail.com/minio/cpu01-bringup/.
* A mender file, that is pushed to mender server


For further help on fly usage please refer to the [fly documentation](https://concourse-ci.org/fly.html).


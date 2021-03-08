# ci.os.lmp

Build definitions and scripts for ci.os.lmp platform.

This repo can host several projects (with different Yocto versions, machines etc.). Each project
has a subdirectory in that repo, e.g. `mender-on-verdin`.

Yocto builds are performed using kas: https://github.com/siemens/kas

# Projects
## mender-on-verdin
![CI](https://concourse.ci4rail.com/api/v1/teams/main/pipelines/mender-on-verdin/jobs/build-mender-on-verdin/badge)

Project to integrate mender.io layer on top of toradex layers for toradex verdin module. It can be either build locally using dobi or via Concourse CI.

See [CI/CD Readme](mender-on-verdin/README.md) for information about the CI/CD build.
Before building this locally using dobi you need to enter your your mender specific data into `mender-on-verdin/config/mender.env`.

## moducop-cpu01

Project to build images for Moducop CPU01

You need to enter your your mender specific data into `moducop-cpu01/config/mender.env`.


## Running developer builds locally

Developer builds are executed via dobi, e.g. for `mender-on-verdin`. 

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

#### Yocto download and shared state cache

By default, Yocto downloads will be placed into the *./downloads* folder, in order
to share the downloads between all projects.

By default, Yocto shared state cache will be placed into *\<project\>/sstate-cache* folder.

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
fly --target prod login --concourse-url https://concourse.ci4rail.com
```

The following steps are performed for each project in this repo e.g. `mender-on-verdin`.
Copy and adapt `ci/credentials.template.yaml` it to your needs. 

```bash
cp ci/credentials.template.yaml ci/credentials-prod.yaml
```

*Note: `ci/config-prod.yaml` is the production configuration. It monitors the directory `yocto` on `master` branch for changes and triggeres a build. Under normal circumstances no modifications are needed in this file. If you want to test something out you can modify and use `ci/config-dev.yaml`*

*Note: `ci/credentials-prod.yaml` and `ci/credentials.yaml` are ignored by git. In this file you can store access credentials and keys that won't be checked in. If you are using some third party vault for credentials that integrates well into concourse, you won't need this file.*


Example for setting the pipeline:
```bash
cd yocto
fly -t prod set-pipeline -c pipeline.yaml -p cpu01-bringup-image -l ci/config-dev.yaml -l ci/credentials.yaml -v name=cpu01-bringup
```

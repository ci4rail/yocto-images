# yocto-images

Build definitions and scripts for Ci4Rail and EdgeFarm Images.

This repo can host several projects (with different Yocto versions, machines etc.).

Yocto builds are performed using [kas](https://github.com/siemens/kas).

## Images

The following images are currently built by this repo.

### edgefarm-image

![CPU01](https://github.com/ci4rail/yocto-images/actions/workflows/cpu01-edgefarm-image.yaml/badge.svg)
![CPU01Plus](https://github.com/ci4rail/yocto-images/actions/workflows/cpu01base-edgefarm-image.yaml/badge.svg)
![Raspberry Pi](https://github.com/ci4rail/yocto-images/actions/workflows/raspberrypi4-64-edgefarm-image.yaml/badge.svg)

An image for the Ci4Rail EdgeFarm Services case including the following features:

- Docker
- Read Only Filesystem
- RW Data Partition
- Mender
- kubeedge

Target Platforms:

- Ci4Rail Moducop CPU01
- Ci4Rail Moducop CPU01Plus
- Raspberry Pi 4

### edgefarm-devtools-image

![CPU01](https://github.com/ci4rail/yocto-images/actions/workflows/cpu01-edgefarm-devtools-image.yaml/badge.svg)
![CPU01Plus](https://github.com/ci4rail/yocto-images/actions/workflows/cpu01base-edgefarm-devtools-image.yaml/badge.svg)
![Raspberry Pi](https://github.com/ci4rail/yocto-images/actions/workflows/raspberrypi4-64-edgefarm-devtools-image.yaml/badge.svg)

An image for the Ci4Rail EdgeFarm Services case including the following features:

- Docker
- Read Only Filesystem
- RW Data Partition
- Mender
- kubeedge
- development tools for edgefarm
- no login password

Target Platforms:

- Ci4Rail Moducop CPU01
- Ci4Rail Moducop CPU01Plus
- Raspberry Pi 4

### devtools-image

![CPU01](https://github.com/ci4rail/yocto-images/actions/workflows/cpu01-devtools-image.yaml/badge.svg)
![CPU01Plus](https://github.com/ci4rail/yocto-images/actions/workflows/cpu01base-devtools-image.yaml/badge.svg)

An image for HW platform tests and bringup including the following features:

- Docker
- Read Only Filesystem
- RW Data Partition
- Mender
- Tools for HW testing
- no login password

Target Platforms:

- Ci4Rail Moducop CPU01
- Ci4Rail Moducop CPU01Plus

## Building

### Github Actions

Images are build automatically via github actions.

The following figure shows which steps are executed for corresponding image types:

![Yocto Images Pipelines](doc/yocto-images-pipelines.drawio.svg)

### Local build

- Enter credentials for mender and/or minio `config/secret.env` (using template `config/secret.env.template`).
- Enter your specific data in `config/custom.env` (using template `config/custom.env.template`).

You can use the default setting, but MENDER_DEVICE_ID must be adapted to the device you want to use to [deploy locally built images via mender](#deploy-images-via-mender)

```bash
MENDER_DEVICE_ID=<device id from mender portal>
```

See [Makefile](Makefile) for build targets and instructions.

## Install Images

### Deploy images via mender

Download [Mender CLI](https://docs.mender.io/downloads#mender-cli)

Login with `mender-cli login`.

Upload image to mender, e.g.:

```bash
make
```

To deploy an image to a specific device set the device ID you want to use for deployment, if you haven't specified it in `config/custom.env`:

```bash
MENDER_DEVICE_ID=<device id from mender portal>
```

Start deployment

```bash
./dobi.sh cpu01-edgefarm-mender-deploy
```

### Deploy images via minio

Upload image to minio, e.g.:

```bash
./dobi.sh cpu01-edgefarm-minio-push
```

To deploy an image to a specific device set the ip address of test-computer connected to this device, if you haven't specified it in `config/custom.env`:

```bash
TEST_COMPUTER_IP=<ip address of test-computer connected to the device to flash>
```

Start deployment

```bash
./dobi.sh cpu01-edgefarm-minio-deploy
```

### Install Raspberry Pi sdimg on SD Card

Find build images:

```bash
ls raspberrypi4-64-edgefarm*-image/install/images/raspberrypi4-64/EdgeFarm*-Image-raspberrypi4-64-*.sdimg
```

Install on SD Card (Linux):

```bash
sudo dd if=<PATH-TO-IMAGE>.sdimg of=<DEVICE> bs=1M && sudo sync
```

Or use [Raspberry Pi Imager](https://www.raspberrypi.org/downloads/):

- Click on `Select OS` and select `Own Image`
- Browse to build images directory and select the *.sdimg file
- Select SD Card to install on
- Press `write`

# yocto-images

Build definitions and scripts for Ci4Rail and EdgeFarm Images.

This repo can host several projects (with different Yocto versions, machines etc.).

Yocto builds are performed using [kas](https://github.com/siemens/kas).

## Images

The following images are currently built by this repo.

### edgefarm-image

![CPU01](https://github.com/ci4rail/yocto-images/actions/workflows/cpu01-edgefarm-image.yaml/badge.svg)
![CPU01Plus](https://github.com/ci4rail/yocto-images/actions/workflows/cpu01plus-edgefarm-image.yaml/badge.svg)
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
![CPU01Plus](https://github.com/ci4rail/yocto-images/actions/workflows/cpu01plus-edgefarm-devtools-image.yaml/badge.svg)
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
![CPU01Plus](https://github.com/ci4rail/yocto-images/actions/workflows/cpu01plus-devtools-image.yaml/badge.svg)

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

### Local build (via Yocto Make GUI)

Run
```
./yocto.sh
```
and choose `image` as target. Then select the image which you would like to build.

### Local build (via Commandline)

- Enter credentials for mender and/or minio in `config/secret.env` (using template `config/secret.env.template`).
- Enter your specific data in `config/custom.env` (using template `config/custom.env.template`).

You can use the default setting, but MENDER_DEVICE_ID must be adapted to the device you want to use to [deploy locally built images via mender](#deploy-images-via-mender)

To build an image, run make with `IMAGE_DIR` set to the image directory, for example:

```bash
make IMAGE_DIR=cpu01-devtools-image image
```

See [Makefile](Makefile) for further build targets and instructions.

## Install Images

Download [Mender CLI](https://docs.mender.io/downloads#mender-cli) and install it to `/usr/local/bin`.
```bash
mv mender-cli /usr/local/bin
```

To deploy an image to a specific device set the device ID you want to use for the deployment in `config/custom.env`:

```bash
MENDER_DEVICE_ID=<device id from mender portal>
```

### Deploy images via mender (via Yocto Make GUI)

Make sure, you entered the mender credentials in `config/secret.env`.

To upload a image to mender, run
```bash
./yocto.sh
```
and choose `mender-upload` as target. Then select the image which you would like to upload.

To deploy a image on your device, run
```bash
./yocto.sh
```
and choose `mender-deploy` as target. Then select the image which you would like to deploy.

You can also use the shortcut target `mender`, which builds the image, uploads it to mender and deploys it on your device.


### Deploy images via mender (via Commandline)

Login with `mender-cli login`.

Upload image to mender, e.g.:

```bash
make IMAGE_DIR=cpu01-devtools-image mender-upload
```

Start deployment

```bash
make IMAGE_DIR=cpu01-devtools-image mender-deploy
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
#
# Makefile for developer builds of yocto-images
#
# Usage:
# $ make IMAGE_DIR=<image-specific-directory> <target>
#
# For example, to build the image
# $ make IMAGE_DIR=cpu01-devtools-image image
#
# All targets (for all, specify the correct IMAGE_DIR):
# - image - build image
# - yocto-shell - start kas with a yocto shell
# - mender-upload - upload latest image to mender server
# - mender-deploy - deploy latest image to device with MENDER_DEVICE_ID
# - mender - combine "image", "mender-upload", "mender-deploy"

include config/custom.env
include config/secret.env
include config/default.env

ABS_IMAGE_DIR := ${shell pwd}/${IMAGE_DIR}
ABS_DOWNLOAD_DIR := ${shell cd ${YOCTO_DOWNLOAD_DIR} && pwd}
ABS_SSTATE_DIR := ${shell cd ${YOCTO_SSTATE_CACHE_DIR} && pwd}
ABS_MENDER_AUTH_DIR = ${shell cd ${MENDER_AUTH_DIR} && pwd}

# Docker images
KAS_IMAGE := "ghcr.io/siemens/kas/kas:2.6.3"
GITVERSION_IMAGE := "elbb/bb-gitversion:0.7.0"
MENDER_CLI_IMAGE := "ci4rail/mender-cli:master"
MINIO_CLI_IMAGE := "minio/mc:latest"
CURL_JQ_IMAGE := "dwdraju/alpine-curl-jq:latest"

# Main-target to build image
# Required variables from command line:
# - IMAGE_DIR
image yocto-shell mender mender-upload mender-deploy: test-main-args
	@echo "Generate image version"
	@docker run --rm \
		-v$(shell pwd):/git \
		-v$(shell pwd)/gen/gitversion:/gen \
		-e USERID="$(shell id -u)" \
		${GITVERSION_IMAGE}
	@scripts/layer-revparse.sh ${IMAGE_DIR}/src ${IMAGE_DIR}/layer-revs
	${MAKE} _$@ IMAGE_VERSION=${shell scripts/gen-image-version.sh . ${IMAGE_DIR}/layer-revs} IMAGE_DIR=${IMAGE_DIR}


#----------------------------------
# Sub-targets
#
# Required variables from main make:
# - IMAGE_DIR
# - IMAGE_VERSION
#
KAS_ARGS := \
		-w=/work \
		-v${ABS_IMAGE_DIR}:/work \
		-v$(shell pwd)/kas-includes:/kas-includes \
		-v$(ABS_DOWNLOAD_DIR):/downloads \
		-v${ABS_SSTATE_DIR}:/sstate-cache \
		-v${ABS_IMAGE_DIR}/install:/install \
		-e MENDER_SERVER_URL=${MENDER_SERVER_URL} \
		-e MENDER_TENANT_TOKEN=${MENDER_TENANT_TOKEN} \
		-e IMAGE_GIT_VERSION=${IMAGE_VERSION} \
		-e MENDER_ARTIFACT_NAME=${IMAGE_DIR}-${IMAGE_VERSION}${NAME_SUFFIX} \
		-e USER_ID="$(shell id -u)" \
		-e GROUP_ID="$(shell id -g)" \
		-e SHELL=/bin/bash \
		-e TERM=xterm-256color

_mender: _image _mender-upload _mender-deploy
	echo ""

_image: test-sub-args _mkdirs
	@echo building image in ${IMAGE_DIR} with version ${IMAGE_VERSION}
	docker run -it --rm ${KAS_ARGS} ${KAS_IMAGE} build kasfile.yaml

_mender-upload: test-sub-args
	docker run --rm -t \
		--user "$(shell id -u):$(shell id -g)" \
		-v${ABS_IMAGE_DIR}/install:/install \
		-v${ABS_MENDER_AUTH_DIR}:/mender-auth \
		--entrypoint=/bin/sh \
		${MENDER_CLI_IMAGE} \
		-c "/mender-cli --token=/mender-auth/authtoken artifacts upload /install/images/*/*[0-9][0-9][0-9][0-9].mender"

_mender-deploy: test-sub-args
	@echo deploying to ${MENDER_DEVICE_ID}
	docker run --rm -t \
		--user "$(shell id -u):$(shell id -g)" \
		-v${ABS_IMAGE_DIR}/install:/install \
		-v${ABS_MENDER_AUTH_DIR}:/mender-auth \
		-v$(shell pwd)/scripts:/scripts \
		-e MENDER_DEVICE_ID=${MENDER_DEVICE_ID} \
		-e MENDER_AUTH_TOKEN=/mender-auth/authtoken \
		-e MENDER_SERVER_URL=${MENDER_SERVER_URL} \
		${CURL_JQ_IMAGE} \
		bash -c "/scripts/mender_accept_new_auth_set.sh && \
		/scripts/mender_deploy_artifact_to_device.sh /install/images/*/*[0-9][0-9][0-9][0-9].mender"

_yocto-shell: test-sub-args  _mkdirs
	@echo starting yocto shell in ${IMAGE_DIR} with version ${IMAGE_VERSION}

	docker run -it --rm ${KAS_ARGS} ${KAS_IMAGE} shell kasfile.yaml

_mkdirs:
	mkdir -p ${IMAGE_DIR}/install ${IMAGE_DIR}/src ${IMAGE_DIR}/build ${ABS_DOWNLOAD_DIR} ${ABS_SSTATE_DIR}

test-main-args:
ifeq (${IMAGE_DIR},)
	${error IMAGE must be set}
endif

test-sub-args:
ifeq (${IMAGE_DIR},)
	${error IMAGE must be set}
endif
ifeq (${IMAGE_VERSION},)
	${error IMAGE must be set}
endif

.PHONY: test-main-args test-sub-args _mkdirs

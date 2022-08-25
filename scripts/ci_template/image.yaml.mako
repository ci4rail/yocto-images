name: ___pipeline_name___
on:
  pull_request:
    branches:
      - main
    paths:
      - "cpu01-devtools-image/kasfile.yaml"
      - "kas-includes/kasfile-tdxbsp.yaml"
      - "kas-includes/kasfile-mender.yaml"
      - "kas-includes/kasfile-mender-tdxbsp.yaml"
      - "kas-includes/kasfile-general.yaml"
      - ".github/workflows/cpu01-devtools-image.yaml"
  push:
    branches:
      - main
    paths:
      - "cpu01-devtools-image/kasfile.yaml"
      - "kas-includes/kasfile-tdxbsp.yaml"
      - "kas-includes/kasfile-mender.yaml"
      - "kas-includes/kasfile-mender-tdxbsp.yaml"
      - "kas-includes/kasfile-general.yaml"
      - ".github/workflows/cpu01-devtools-image.yaml"

env:
  IMAGE_TYPE: devtools
  MACHINE: cpu01
  BUILDER_IMAGE: ghcr.io/siemens/kas/kas:2.6.3
  MACHINE_DIR: moducop-cpu01
  IMAGE_START: Moducop-CPU01_Devtools-Image
  TARFILE_ENDING: mender_tezi
  MENDER_CLI_IMAGE: ci4rail/mender-cli:master-2021-03-08
  ROBOT_IMAGE: ci4rail/robot:latest-2021-03-25
  TEST_COMPUTER_IP: 192.168.24.12
  DOCKER_USERNAME: ci4rail

jobs:
  calculate-version:
    name: Calculate Version
    # Currently not working with Ubuntu 20.04
    runs-on: ubuntu-18.04
    outputs:
      #0.1.0-36.cleanup.e3132eb42a917c62b1f9198def62e78a4346c35d
      version: ${{ steps.gitversion.outputs.majorMinorPatch }}-${{ steps.gitversion.outputs.commitsSinceVersionSource }}.${{ github.event.pull_request.head.ref }}.${{ steps.shortSha.outputs.shortSha }}
      name_suffix: ${{ steps.eventCheck.outputs.nameSuffix }}
    steps:
      # Checks-out your repository under $GITHUB_WORKSPACE, so your job can access it
      - uses: actions/checkout@v2
        with:
          fetch-depth: 0
      - name: Install GitVersion
        uses: gittools/actions/gitversion/setup@v0.9.7
        with:
          versionSpec: "5.x"
      - name: Determine Version
        id: gitversion
        uses: gittools/actions/gitversion/execute@v0.9.7
      - name: Get git sha as on PR gitversion provides incorrect sha
        id: shortSha
        env:
          EVENT_NAME: ${{ github.event_name }}
        run: |
          if [ "$EVENT_NAME" = "pull_request" ]; then
              echo "::set-output name=shortSha::`echo ${{ github.event.pull_request.head.sha }} | cut -c1-8`"
          elif [ "$EVENT_NAME" = "push" ]; then
              echo "::set-output name=shortSha::${GITHUB_SHA::8}"
          else
              echo "::set-output name=shortSha::invalid"
          fi
      - name: Generate Image name dependend on github event
        id: eventCheck
        env:
          EVENT_NAME: ${{ github.event_name }}
        run: |
          if [ "$EVENT_NAME" = "pull_request" ]; then
              echo "::set-output name=nameSuffix::-pr"
          elif [ "$EVENT_NAME" = "push" ]; then
              echo "::set-output name=nameSuffix::"
          else
              echo "::set-output name=nameSuffix::-invalid"
          fi

  build-and-push-image:
    needs: calculate-version
    name: Build and Push Image
    runs-on: [self-hosted, linux, x64, yocto-runner]
    outputs:
      bucket: ${{ env.MACHINE }}-${{ env.IMAGE_TYPE }}${{ needs.calculate-version.outputs.name_suffix }}
      nameSuffix: ${{ needs.calculate-version.outputs.name_suffix }}
      version: ${{ needs.calculate-version.outputs.version }}
    steps:
      # Checks-out your repository under $GITHUB_WORKSPACE, so your job can access it
      - uses: actions/checkout@v2

      - name: Get Cache for Yocto Builds
        if: github.event_name == 'pull_request'
        run: |
          rsync -rht --ignore-missing-args /yocto-cache/downloads/* /downloads/
          rsync -rht --ignore-missing-args /yocto-cache/sstate-cache/* /sstate-cache/

      - name: Log into Docker
        env:
          DOCKER_PASSWORD: ${{ secrets.DOCKER_PASSWORD }}
        run: echo ${DOCKER_PASSWORD} | docker login ${DOCKER_REGISTRY} -u ${DOCKER_USERNAME} --password-stdin

      - name: Build Yocto
        env:
          # Required internally for build
          MENDER_SERVER_URL: https://hosted.mender.io
          MENDER_TENANT_TOKEN: ${{ secrets.MENDER_TENANT_TOKEN }}
          IMAGE_NAME_SUFFIX: ${{ needs.calculate-version.outputs.name_suffix }}
          IMAGE_GIT_VERSION: ${{ needs.calculate-version.outputs.version }}
          MENDER_ARTIFACT_NAME: ${{ env.MACHINE }}-${{ env.IMAGE_TYPE }}-${{ needs.calculate-version.outputs.version }}${{ needs.calculate-version.outputs.name_suffix }}
        run: |
          docker run \
                -v /install:/install \
                -v /sstate-cache:/sstate-cache \
                -v /downloads:/downloads \
                -v ${GITHUB_WORKSPACE}/${MACHINE}-${IMAGE_TYPE}-image:/work \
                -v ${GITHUB_WORKSPACE}/kas-includes:/kas-includes \
                -e USER_ID=$(id -u ${USER}) \
                -e GROUP_ID=$(id -g ${USER}) \
                -e MENDER_SERVER_URL=${MENDER_SERVER_URL} \
                -e MENDER_TENANT_TOKEN=${MENDER_TENANT_TOKEN} \
                -e IMAGE_NAME_SUFFIX=${IMAGE_NAME_SUFFIX} \
                -e IMAGE_GIT_VERSION=${IMAGE_GIT_VERSION} \
                -e MENDER_ARTIFACT_NAME=${MENDER_ARTIFACT_NAME} \
                -w /work \
                ${BUILDER_IMAGE} \
                build kasfile.yaml

      - name: Store Cache for Yocto Builds
        if: github.event_name == 'pull_request'
        run: |
          rsync -rht /downloads/* /yocto-cache/downloads/
          rsync -rht /sstate-cache/* /yocto-cache/sstate-cache/

      - name: Check Build Artefacts are there
        id: artefacts
        env:
          TAR_IMAGE_PATH: /install/images/${{ env.MACHINE_DIR }}/${{ env.IMAGE_START }}_${{ needs.calculate-version.outputs.version }}${{ needs.calculate-version.outputs.name_suffix }}.${{ env.TARFILE_ENDING }}.tar
          MENDER_IMAGE_PATH: /install/images/${{ env.MACHINE_DIR }}/${{ env.IMAGE_START }}_${{ needs.calculate-version.outputs.version }}${{ needs.calculate-version.outputs.name_suffix }}.mender
        run: |
          ls -t ${TAR_IMAGE_PATH}
          mkdir ${GITHUB_WORKSPACE}/minio
          cp ${TAR_IMAGE_PATH} ${GITHUB_WORKSPACE}/minio
          cp ${MENDER_IMAGE_PATH} ${GITHUB_WORKSPACE}/minio
          ls -t ${MENDER_IMAGE_PATH}

      - name: Push Images to Minio
        uses: hkdobrev/minio-deploy-action@v1
        with:
          endpoint: https://minio.ci4rail.com
          access_key: ${{ secrets.MINIO_ACCESS_KEY }}
          secret_key: ${{ secrets.MINIO_SECRET_KEY }}
          bucket: ${{ env.MACHINE }}-${{ env.IMAGE_TYPE }}${{ needs.calculate-version.outputs.name_suffix }}
          source_dir: minio
          target_dir: "/"
      - name: Push Images to Mender
        env:
          MENDER_USER_EMAIL: ${{ secrets.MENDER_USER_EMAIL }}
          MENDER_PASSWORD: ${{ secrets.MENDER_PASSWORD }}
          IMAGE_GIT_VERSION: ${{ needs.calculate-version.outputs.version }}
          IMAGE_NAME_SUFFIX: ${{ needs.calculate-version.outputs.name_suffix }}
        run: |
          docker run \
            -v /install:/install \
            --entrypoint=/bin/sh \
            ${MENDER_CLI_IMAGE} -c "
              /mender-cli login --username ${MENDER_USER_EMAIL} --password ${MENDER_PASSWORD}
              /mender-cli artifacts upload /install/images/${MACHINE_DIR}/${IMAGE_START}_${IMAGE_GIT_VERSION}${IMAGE_NAME_SUFFIX}.mender
            "

  test-image:
    needs: [calculate-version, build-and-push-image]
    name: Test Yocto Image
    runs-on: ubuntu-latest
    steps:
      - uses: ci4rail/teststation-action@v1
        with:
          pipeline-name: "${{ github.repository }}"
          # This name is resolved by tailscale magic DNS
          mqtt-broker-url: "lizard-rpi:1883"
          test-name: "cpu01-devtools-image test"
          artifact-override: |
            {
              \"desired_versions.cpu_firmware.version\": \"${{ env.IMAGE_START }}_${{ needs.calculate-version.outputs.version }}${{ needs.calculate-version.outputs.name_suffix }}\",
              \"desired_versions.cpu_firmware.method\": \"toradex-installer\",
              \"desired_versions.cpu_firmware.source.type\": \"ci4rail-minio\",
              \"desired_versions.cpu_firmware.source.bucket\": \"${{ needs.build-and-push-image.outputs.bucket }}\",
              \"desired_versions.cpu_firmware.source.file\": \"${{ env.IMAGE_START }}_${{ needs.calculate-version.outputs.version }}${{ needs.calculate-version.outputs.name_suffix }}.${{ env.TARFILE_ENDING }}.tar\"
            }
          access-token: ${{ secrets.FW_CI_TOKEN }}
          tailscale-key: ${{ secrets.YODA_TAILSCALE_AUTHKEY }}
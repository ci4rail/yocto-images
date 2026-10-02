# Host-only tooling. No signing credentials or recovery outputs enter image layers.
FROM ubuntu:24.04 AS tools
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git curl build-essential cmake pkg-config bison flex \
    uuid-dev libssl-dev libgnutls28-dev libcurl4-openssl-dev libjson-c-dev libp11-kit-dev \
    libengine-pkcs11-openssl zlib1g-dev python3 python3-cryptography python3-boto3 \
    device-tree-compiler squashfs-tools gcc-aarch64-linux-gnu libc6-dev-arm64-cross \
    && rm -rf /var/lib/apt/lists/*
# v2024.07: includes fit_check_sign, which distro u-boot-tools does not ship.
RUN git init /opt/u-boot && cd /opt/u-boot \
    && git remote add origin https://github.com/u-boot/u-boot.git \
    && git fetch --depth 1 origin 3f772959501c99fbe5aa0b22a36efe3478d1ae1c \
    && git checkout --detach FETCH_HEAD \
    && make tools-only_defconfig \
    && make -j4 tools-only NO_SDL=1 \
    && install tools/mkimage tools/dumpimage tools/fit_check_sign /usr/local/bin/ \
    && rm -rf /opt/u-boot
WORKDIR /work

FROM tools AS production
# Same source revisions and patches as the production Yocto signing layer.
RUN git init /opt/meta-akv-signing && cd /opt/meta-akv-signing \
    && git remote add origin https://github.com/ci4rail/meta-akv-signing.git \
    && git fetch --depth 1 origin 0759e3c060991f82c5d51ba67b8776fbf491f838 \
    && git checkout --detach FETCH_HEAD
RUN git init /opt/azure-sdk && cd /opt/azure-sdk \
    && git remote add origin https://github.com/Azure/azure-sdk-for-cpp.git \
    && git fetch --depth 1 origin 35e7740903015383c79d271c0baefedc593fb212 \
    && git checkout --detach FETCH_HEAD \
    && git apply /opt/meta-akv-signing/recipes-support/azure-sdk-cpp/files/build-required-libraries-only.patch \
    && AZURE_SDK_DISABLE_AUTO_VCPKG=1 cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
       -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DWARNINGS_AS_ERRORS=OFF \
       -DBUILD_TESTING=OFF -DAZ_ALL_LIBRARIES=OFF -DDISABLE_AMQP=ON \
       -DDISABLE_AZURE_CORE_OPENTELEMETRY=ON -DBUILD_TRANSPORT_CURL=ON \
       -DBUILD_TRANSPORT_WINHTTP=OFF \
       '-DAZ_BUILD_ONLY=azure-core;azure-identity;azure-security-keyvault-keys;azure-security-keyvault-certificates' \
    && cmake --build build -j4 && cmake --install build \
    && rm -rf /opt/azure-sdk
RUN git init /opt/akv && cd /opt/akv \
    && git remote add origin https://github.com/jepio/azure-keyvault-pkcs11.git \
    && git fetch --depth 1 origin c72d89bf0b17f8c21a93870efaaabb93c0dc9c63 \
    && git checkout --detach FETCH_HEAD \
    && git apply /opt/meta-akv-signing/recipes-support/azure-keyvault-pkcs11/files/0001-limit-credential-chain-to-non-interactive-ci-sources.patch \
    && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_LIBDIR=lib \
    && cmake --build build -j4 && cmake --install build \
    && ldconfig && rm -rf /opt/akv
ENV PKCS11_MODULE_PATH=/usr/local/lib/pkcs11/azure-keyvault-pkcs11.so

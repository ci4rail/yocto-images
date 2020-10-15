FROM ubuntu:18.04
LABEL maintainer="info@ci4rail.de"
LABEL description="This is a builder image for toradex yocto"
LABEL vendor="Ci4Rail"
LABEL uri="https://github.com/ci4rail/techpoc"

# bitbake requires a utf8 filesystem encoding
ENV LANG en_US.UTF-8
ENV LANGUAGE en_US:en
ENV LC_ALL en_US.UTF-8

ARG DEBIAN_FRONTEND=noninteractive

# OSF PPA for additional dependencies and newer packages
RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
	software-properties-common \
	&& add-apt-repository ppa:osf-maintainers/ppa \
	&& apt-get clean \
	&& rm -rf /var/lib/apt/lists/*

RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
	android-tools-fsutils ca-certificates chrpath cpio diffstat \
	file gawk g++ iproute2 iputils-ping less libmagickwand-dev \
	libmath-prime-util-perl libsdl1.2-dev libssl-dev locales \
	openjdk-8-jre openssh-client perl-modules python2.7 python-requests python3 \
	repo sudo texinfo vim-tiny wget whiptail libelf-dev gosu \
	&& apt-get autoremove -y \
	&& apt-get clean \
	&& rm -rf /var/lib/apt/lists/* \
	&& locale-gen en_US.UTF-8

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY scripts/prepare.sh /usr/local/bin/prepare.sh
RUN chmod +x /usr/local/bin/prepare.sh

COPY scripts/build.sh /usr/local/bin/build.sh
RUN chmod +x /usr/local/bin/build.sh

COPY scripts/prepare_and_build.sh /usr/local/bin/prepare_and_build.sh
RUN chmod +x /usr/local/bin/prepare_and_build.sh

COPY config/image_list.json /etc/image_list.json

WORKDIR /workdir

ARG YOCTO_BUILDER_VERSION=unversioned-dont-use
ENV YOCTO_BUILDER_VERSION=${YOCTO_BUILDER_VERSION}

ENTRYPOINT [ "/usr/local/bin/entrypoint.sh" ]
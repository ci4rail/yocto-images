#!/bin/bash

# Downloads dobi in specific version and for target OS
download_dobi() {
    if [[ "$OSTYPE" == "linux-gnu"* || "$OSTYPE" == "cygwin" ]]; then
            dobi_dltarget=linux
        elif [[ "$OSTYPE" == "darwin"* ]]; then
            dobi_dltarget=darwin
        elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
            dobi_dltarget=windows
        fi
        if [[ ! -z ${dobi-dltarget} && -x $(which curl) ]]; then
            mkdir -p .dobi
            curl -L --progress-bar --fail -o ${1} "https://github.com/dnephin/dobi/releases/download/v${DOWNLOAD_VERSION_DOBI}/dobi-${dobi_dltarget}"
            if [[ ${?} -ne 0 ]]; then
                echo "Download of dobi failed. Check specified version in env variable \"DOWNLOAD_VERSION_DOBI\"".
                exit 1
            else
                chmod +x ${1}
            fi
        else
            echo "\"dobi\" not found. Please install \"dobi\": https://github.com/dnephin/dobi/releases"
            exit 1
        fi
}

if [ -f .env ]; then
  source .env
  if [[ ! -z "${YOCTO_SSTATE_CACHE_DIR}" ]]; then
    if [[ "${YOCTO_SSTATE_CACHE_DIR}" != *\/ ]]; then
      echo "Error: make sure that value for YOCTO_SSTATE_CACHE_DIR ends with  '/'"
      exit 1
    fi
else
    # Case: dobi present in this project
    dobi=${DOBI_THIS_PROJECT}
    PRESENT_DOBI_VERSION=$(dobi_version ${dobi})
    if [[ ${PRESENT_DOBI_VERSION} != *${DOWNLOAD_VERSION_DOBI}* ]]; then
        # Case: Version mismatch between 'requested' and 'present in project'
        echo "Found incorrect version for '${DOBI_THIS_PROJECT}': ${PRESENT_DOBI_VERSION}. Downloading version ${DOWNLOAD_VERSION_DOBI}."
        download_dobi ${DOBI_THIS_PROJECT} ${DOWNLOAD_VERSION_DOBI}
    fi
fi

# generate actual version files
${dobi} --filename meta.yaml version

# check argument count
if [ -z ${1} ]; then
    echo "no arguments!"
    dobi --filename meta.yaml list
    exit 1
fi

# load generated version infos
source gen/gitversion/env/gitversion.env

if [ -f default.env ]; then
  source default.env
  if [[ ! -z "${YOCTO_SSTATE_CACHE_DIR}" ]]; then
    if [[ "${YOCTO_SSTATE_CACHE_DIR}" != *\/ ]]; then
      echo "Error: make sure that value for YOCTO_SSTATE_CACHE_DIR ends with  '/'"
      exit 1
    fi
  fi
fi

# execute dobi with meta as default
exec dobi --filename meta.yaml ${@}

#!/bin/bash
# create a deployment for a single device

set -o errexit

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <mender_filename>" >&2
  exit 1
fi

if [ -z "${MENDER_DEVICE_ID}" ]; then 
  echo "Please set MENDER_DEVICE_ID" >&2
  exit 1
fi

if [ -z "${MENDER_AUTH_TOKEN}" ]; then 
  echo "Please set MENDER_AUTH_TOKEN" >&2
  exit 1
fi

if [ ! -f ${MENDER_AUTH_TOKEN} ]; then
  echo "File ${MENDER_AUTH_TOKEN} does not exist" >&2
  exit 1
fi
JWT=$(cat ${MENDER_AUTH_TOKEN})

device_id=${MENDER_DEVICE_ID}
MENDER_SERVER_URL=https://hosted.mender.io

mender_filename=${1}
if [ ! -f ${mender_filename} ]; then
  echo "File ${mender_filename} does not exit" >&2
  exit 1
fi
echo "Mender filename is "${mender_filename}

artifact_name=$(tar xOf ${mender_filename} header.tar.gz | tar zxO | jq '.artifact_provides.artifact_name' | grep -v null | tr -d '"')
echo "Artifact name is "${artifact_name}

deployment_name="depl-${device_id}-$(date +%Y-%m-%d-%H:%M:%S)"
echo "Deployment name is "${deployment_name}

json="{ 
  \"name\": \"${deployment_name}\", 
  \"artifact_name\": \"${artifact_name}\", 
  \"devices\": [ 
    \"${device_id}\" 
  ] 
 }"

curl -H "Authorization: Bearer ${JWT}" -H 'Content-Type: application/json' -H 'Accept: application/json' \
 -X POST  ${MENDER_SERVER_URL}/api/management/v1/deployments/deployments  -d "${json}"


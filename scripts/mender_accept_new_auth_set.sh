#!/bin/bash
# In case a device was deployed via minio, the public key on mender changes and so the device is not able to connect anymore.
# Therfor it is required to re-accept the new public key.

set -o errexit

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

deviceStatus=$(curl -H "Authorization: Bearer ${JWT}" -H 'Accept: application/json' -X GET  ${MENDER_SERVER_URL}/api/management/v2/devauth/devices/${MENDER_DEVICE_ID})

if ($(echo $deviceStatus | jq 'has("error")')); then
  error=$(echo $deviceStatus | jq -c '.error' | sed 's/"//g')
  if [ "$error" = "invalid jwt" ]; then
    echo "Error, jwt expired, please run 'mender-cli login'"
  else
    echo "Error, recieved error response from mender: $error"
  fi
  exit 1
fi

sortedAuthSets=$(echo $deviceStatus | jq '[.auth_sets | sort_by(.ts)| reverse[]]' )

# Always accept the first request, which is the latest one
authSet=$(echo $sortedAuthSets | jq -c '.[0]')
status=$(echo $authSet | jq -c '.status' | sed 's/"//g')
aid=$(echo $authSet | jq -c '.id' | sed 's/"//g')
ts=$(echo $authSet | jq -c '.ts' | sed 's/"//g')
if [ "$status" = "pending" ]; then
  echo Accept pending auth request with AuthID $aid from $ts
  json="{
    \"status\": \"accepted\"
  }"
  curl -H "Authorization: Bearer ${JWT}" \
       -H 'Content-Type: application/json' \
       -H 'Accept: application/json' \
       -X PUT \
       ${MENDER_SERVER_URL}/api/management/v2/devauth/devices/${MENDER_DEVICE_ID}/auth/${aid}/status \
       -d "${json}"
else
  echo No pending auth request available
fi
exit 0

echo "Error, device with MENDER_DEVICE_ID ${MENDER_DEVICE_ID} not found"
exit 1

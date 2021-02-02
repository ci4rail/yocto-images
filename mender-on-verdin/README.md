![CI](https://concourse.ci4rail.com/api/v1/teams/main/pipelines/mender-on-verdin/jobs/build-mender-on-verdin/badge)

## Using Azure iotedge
In order to use Azure iotedge the `device_connection_string` and `hostname` must be input in `/etc/iotedge/config.yaml` after the device has successfully booted. Either connect via `ssh` or serial terminal and make the modifications.

Follow these steps (see https://docs.microsoft.com/en-us/azure/iot-edge/quickstart-linux for further information) to manually provision the IoT Edge device:

```bash
$ DEVCE_NAME=mydeviceid
$ IOT_HUB=myiothub
$ az iot hub device-identity create --device-id ${DEVCE_NAME} --edge-enabled --hub-name ${IOT_HUB}
$ az iot hub device-identity connection-string show --device-id ${DEVCE_NAME} --hub-name ${IOT_HUB} 
{
  "connectionString": "HostName=myiothub.azure-devices.net;DeviceId=mydeviceid;SharedAccessKey=bAI2xoV.........................KjdDwk9IuNg="
} 
```

After you entered the `device_connection_string` and `hostname` either reboot or run `systemctl restart iotedge`.

## Updating the pipeline

With valid config and credentials files you are can deploy the pipeline to concourse.

```bash
fly -t prod set-pipeline -c pipeline-prod.yaml -p mender-on-verdin -l ci/config-prodyaml -l ci/credentials-prod.yaml
```

Initially set pipelines are paused by default. You can unpause them either via the web interace or via fly.

```bash
fly -t prod unpause-pipeline -p mender-on-verdin
```

For further help on fly usage please refer to the [fly documentation](https://concourse-ci.org/fly.html).

## Accessing build artifacts

The build artifacts are stored here: https://minio.ci4rail.com/minio/mender-on-verdin/mender-on-verdin/.

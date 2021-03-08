![CI](https://concourse.ci4rail.com/api/v1/teams/main/pipelines/mender-on-verdin/jobs/build-mender-on-verdin/badge)

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

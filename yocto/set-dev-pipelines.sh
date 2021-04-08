#!/bin/bash

for image in cpu01-bringup cpu01-edgefarm verdindev-edgefarm; do
    fly -t dev set-pipeline -c pipeline.yaml -p ${image}-dev -l ci/config-dev.yaml -l ci/credentials.yaml -v name=${image}
    fly -t dev unpause-pipeline -p ${image}-dev
done

fly -t dev set-pipeline -c pipeline_testing.yaml -p cpu01-edgefarm-acceptance-test -l ci/config-testing.yaml ci/config-dev.yaml -l ci/credentials.yaml -v name=cpu01-edgefarm

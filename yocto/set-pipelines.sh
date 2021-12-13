#!/bin/bash

# testing-cpu01-edgefarm
fly -t dev set-pipeline -c pipeline-acctest.yaml -p testing-cpu01-edgefarm \
    -l ci/config-global.yaml -l ci/credentials.yaml -l ci/config-testing.yaml -l ci/config-image-test.yaml \
    -v name=cpu01-edgefarm
fly -t dev unpause-pipeline -p testing-cpu01-edgefarm


# ci.os.lmp-pull-requests
fly -t dev set-pipeline -c pipeline-pullrequests.yaml -p ci.os.lmp-pull-requests \
    -l ci/config-global.yaml -l ci/credentials.yaml -l ci/config-image-test.yaml \
    -v name=cpu01-base
fly -t dev unpause-pipeline -p ci.os.lmp-pull-requests

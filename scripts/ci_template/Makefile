#OUT_DIR = ../../.github/workflows
OUT_DIR = test
TEMPLATE = image.yaml.mako

all: ${OUT_DIR}/cpu01-base-image.yaml


${OUT_DIR}/cpu01-base-image.yaml: ${TEMPLATE} cpu01-base.yaml
	m2cli -t ${TEMPLATE} -d cpu01-base.yaml -o $@

.PHONY: all 

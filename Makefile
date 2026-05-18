.PHONY: help serve validate kubeconform shellcheck all

PORT ?= 3007

help:
	@echo "make serve        - serve the docsify site on localhost:$(PORT)"
	@echo "make validate     - YAML parse + shape check over every manifest"
	@echo "make kubeconform  - schema-validate manifests (needs kubeconform installed)"
	@echo "make shellcheck   - run shellcheck on serve.sh"
	@echo "make all          - validate"

serve:
	python3 -m http.server $(PORT)

validate:
	python3 tools/check_manifests.py

# Deeper schema validation if you have kubeconform on PATH. Some labs reference
# CRDs (ServiceMonitor, the Website CRD), so missing-schema kinds are skipped.
kubeconform:
	@command -v kubeconform >/dev/null || { echo "kubeconform not installed"; exit 1; }
	kubeconform -summary -ignore-missing-schemas \
		$$(find kubernetes-labs -name '*.yaml')

shellcheck:
	@command -v shellcheck >/dev/null || { echo "shellcheck not installed"; exit 1; }
	shellcheck -S error serve.sh

all: validate

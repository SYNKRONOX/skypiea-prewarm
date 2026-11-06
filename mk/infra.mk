# Skypiea infra targets: tool images, kind, AWS (Terraform + k3s + ECR), E0.
# Included from the root Makefile:   include mk/infra.mk
.PHONY: build-images measure-startup infra-test cluster-kind cluster-kind-down push-images-kind \
        deploy-kind e0-kind aws-plan aws-apply aws-kubeconfig push-images-aws \
        deploy-aws e0-aws aws-stop aws-start aws-destroy infra-check aws-tunnel aws-shell \
        aws-reminder aws-gateway e0-kind-parallel e0-aws-parallel tool-map-coverage \
        deploy-kind-e0 kind-integration grafana-kind e0-aws-node e0-aws-status e0-aws-fetch \
        push-images-aws-docker clean-images

PY          ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
IMAGE_TAG   ?= $(shell tools/image_tag.sh)
TOOL_LIST    = $(shell $(PY) -c 'import yaml;print(" ".join(yaml.safe_load(open("tools/tool_map.yaml"))["servers"]))')
IMAGES       = $(TOOL_LIST) e0-probe platform
CHART        = charts/skypiea
NS           = skypiea-tools
CLUSTER     ?= skypiea
REG_PORT    ?= 5001
REG_NAME    ?= kind-registry
KIND_CTX    ?= kind-$(CLUSTER)
KIND_REG    ?= localhost:$(REG_PORT)
PF_BASE     ?= 18080
GRAFANA_PORT ?= 3000
# the kind/demo scripts read these from the environment (tests use another cluster and ports)
export CLUSTER REG_PORT REG_NAME KIND_CTX KIND_REG PF_BASE GRAFANA_PORT
N           ?= 10
# AWS deploy: E0 first (no controller); afterwards PLATFORM=true E0PROBE=false
PLATFORM    ?= false
E0PROBE     ?= true
PLATFORM_TAG ?= $(IMAGE_TAG)
# real predictor (optional): PREDICTOR_TAG=aabdfd2a9a39 PROFILE=support|coding SERVE=stub|model
PREDICTOR_TAG ?=
PROFILE     ?= support
SERVE       ?= stub
TF           = terraform -chdir=infra/aws
AWS_KUBECONFIG ?= $(HOME)/.kube/skypiea-aws
AWS_REGION  ?= ap-south-1
REMIND_H    ?= 8

# ---------------- images ----------------
build-images:            ## build all tool images + E0 probe, one at a time
	tools/build_images.sh

measure-startup:         ## image size + startup time per tool (plain docker)
	$(PY) tools/measure_startup.py --n 5

clean-images:            ## remove the local skypiea/* images (and their localhost:<port>/ copies) from Docker
	tools/clean_images.sh

# ---------------- kind ----------------
cluster-kind:            ## kind cluster "skypiea" (1 control-plane + 1 worker) + localhost:5001
	deploy/kind/up.sh

cluster-kind-down:
	deploy/kind/down.sh

push-images-kind:
	@for i in $(IMAGES); do \
	  docker tag skypiea/$$i:$(IMAGE_TAG) $(KIND_REG)/skypiea/$$i:$(IMAGE_TAG) && \
	  docker push -q $(KIND_REG)/skypiea/$$i:$(IMAGE_TAG) >/dev/null && echo "pushed $$i" || exit 1; \
	done

deploy-kind: push-images-kind  ## helm install skypiea on kind: tools at 0 + gateway, controller, predictor
	helm upgrade --install skypiea $(CHART) --kube-context $(KIND_CTX) \
	  -n $(NS) --create-namespace -f $(CHART)/values-kind.yaml \
	  --set registry=$(KIND_REG)/ --set imageTag=$(IMAGE_TAG) --set platform.enabled=true --set e0Probe.enabled=false --wait
	kubectl --context $(KIND_CTX) -n $(NS) get deploy

deploy-kind-e0: push-images-kind  ## tools + E0 probe, NO controller (E0 scales tools itself)
	helm upgrade --install skypiea $(CHART) --kube-context $(KIND_CTX) \
	  -n $(NS) --create-namespace -f $(CHART)/values-kind.yaml \
	  --set registry=$(KIND_REG)/ --set imageTag=$(IMAGE_TAG) --set platform.enabled=false --set monitoring.enabled=false \
	  --set e0Probe.enabled=true --wait
	kubectl --context $(KIND_CTX) -n $(NS) get deploy

e0-kind:
	$(PY) -m experiments.e0_cold_start --target kind --n $(N) --modes cached,uncached --tools all

# ---------------- AWS ----------------
# Every aws-* target first runs infra/aws/scripts/aws-guard.sh: it refuses, before any AWS or
# Terraform call, when aws/terraform are missing or no AWS credentials are configured (env
# vars, or the files ~/.aws/credentials / ~/.aws/config exist; their contents are not read).
# Targets that create or start billable resources also need AWS_CONFIRM=yes.
AWS_CONFIRM ?=
AWS_GUARD = infra/aws/scripts/aws-guard.sh
AWS_GUARD_CREATE = AWS_CONFIRM=$(AWS_CONFIRM) infra/aws/scripts/aws-guard.sh --create
# No SSH and no inbound port: shell, k3s API and the gateway demo all go through SSM Session
# Manager (needs session-manager-plugin on the laptop). Run `make aws-tunnel` in its own
# terminal before anything that uses kubectl/helm (deploy-aws, e0-aws, aws-gateway, skypiea CLI).
aws-plan:                ## terraform init/validate/fmt-check + plan -> infra/aws/tfplan (never committed)
	@$(AWS_GUARD) $@
	$(TF) init -input=false
	$(TF) validate
	$(TF) fmt -check -recursive
	$(TF) plan -input=false -out=tfplan

aws-apply:               ## apply the saved, reviewed plan exactly; then write the destroy reminder
	@$(AWS_GUARD_CREATE) $@
	$(TF) apply -input=false tfplan
	@rm -f infra/aws/tfplan
	@infra/aws/scripts/destroy-reminder.sh $(REMIND_H)

aws-reminder:            ## calendar file reminding you to destroy, REMIND_H hours from now (default 8)
	@infra/aws/scripts/destroy-reminder.sh $(REMIND_H)

aws-tunnel:              ## SSM port-forward 127.0.0.1:16443 -> k3s API (foreground; own terminal)
	@$(AWS_GUARD) $@
	infra/aws/scripts/tunnel.sh

aws-shell:               ## interactive root-capable shell on the VM over SSM (no SSH)
	@$(AWS_GUARD) $@
	aws ssm start-session --region $(AWS_REGION) --target $$($(TF) output -raw instance_id)

aws-kubeconfig:          ## fetch kubeconfig over SSM -> ~/.kube/skypiea-aws (outside the repo)
	@$(AWS_GUARD) $@
	KUBECONFIG_OUT=$(AWS_KUBECONFIG) infra/aws/scripts/get-k3s-config.sh

push-images-aws:         ## copy all images kind registry -> ECR (infra/aws/scripts/push_ecr.py; run push-images-kind first)
	@$(AWS_GUARD_CREATE) $@
	$(PY) infra/aws/scripts/push_ecr.py --registry $$($(TF) output -raw ecr_registry) --region $(AWS_REGION) \
	  --tag $(IMAGE_TAG) --images $(IMAGES)

push-images-aws-docker:  ## fallback: docker login + docker push (slow here via Docker Desktop's proxy)
	@$(AWS_GUARD_CREATE) $@
	@REG=$$($(TF) output -raw ecr_registry); \
	aws ecr get-login-password --region $(AWS_REGION) | \
	  docker login --username AWS --password-stdin $$REG >/dev/null && echo "logged in to $$REG"; \
	for i in $(IMAGES); do \
	  docker tag skypiea/$$i:$(IMAGE_TAG) $$REG/skypiea/$$i:$(IMAGE_TAG) && \
	  docker push -q $$REG/skypiea/$$i:$(IMAGE_TAG) >/dev/null && echo "pushed $$i" || exit 1; \
	done; \
	docker logout $$REG >/dev/null

deploy-aws:              ## helm install the chart on k3s (all tools at 0); needs aws-tunnel
	@$(AWS_GUARD) $@
	helm upgrade --install skypiea $(CHART) --kubeconfig $(AWS_KUBECONFIG) \
	  -n $(NS) --create-namespace -f $(CHART)/values-aws.yaml \
	  --set registry=$$($(TF) output -raw ecr_registry)/ \
	  --set imageTag=$(IMAGE_TAG) --set platformImageTag=$(PLATFORM_TAG) \
	  --set platform.enabled=$(PLATFORM) \
	  $(if $(PREDICTOR_TAG),--set platform.predictor.image.tag=$(PREDICTOR_TAG) \
	    --set-file platform.predictor.coldStartYaml=configs/cold_start.yaml \
	    --set platform.predictor.profile=$(PROFILE) --set platform.predictor.serve=$(SERVE)) \
	  --set monitoring.enabled=$(PLATFORM) --set e0Probe.enabled=$(E0PROBE) --wait
	kubectl --kubeconfig $(AWS_KUBECONFIG) -n $(NS) get deploy

e0-aws:                  ## E0 on AWS (needs aws-tunnel; image removal goes over SSM)
	@$(AWS_GUARD) $@
	$(PY) -m experiments.e0_cold_start --target aws --n $(N) --modes cached,uncached \
	  --tools all --kubeconfig $(AWS_KUBECONFIG)

e0-aws-node:             ## E0 ON the node (systemd unit; seq n=$(N) then parallel-3); uploads to S3, then stops the VM
	@$(AWS_GUARD_CREATE) $@
	IMAGE_TAG=$(IMAGE_TAG) N=$(N) PY=$(PY) infra/aws/scripts/e0-node.sh start

e0-aws-status:           ## progress of the on-node E0 job (one short SSM call; S3 if the VM is stopped)
	@$(AWS_GUARD) $@
	infra/aws/scripts/e0-node.sh status $(RUN_ID)

e0-aws-fetch:            ## fetch on-node E0 results from S3, verify, mask account id, make reports
	@$(AWS_GUARD) $@
	PY=$(PY) infra/aws/scripts/e0-node.sh fetch $(RUN_ID)

aws-gateway:             ## demo: gateway on http://127.0.0.1:8080/mcp (laptop loopback only); needs aws-tunnel
	@$(AWS_GUARD) $@
	kubectl --kubeconfig $(AWS_KUBECONFIG) -n $(NS) port-forward --address 127.0.0.1 svc/skypiea-gateway 8080:80

aws-stop:                ## stop the VM (keeps disk, ECR, S3; EBS storage still billed)
	@$(AWS_GUARD) $@
	aws ec2 stop-instances --region $(AWS_REGION) --instance-ids $$($(TF) output -raw instance_id) \
	  --query 'StoppingInstances[0].CurrentState.Name' --output text

aws-start:               ## start the VM again (kubeconfig is unchanged: it points at the tunnel)
	@$(AWS_GUARD_CREATE) $@
	aws ec2 start-instances --region $(AWS_REGION) --instance-ids $$($(TF) output -raw instance_id) \
	  --query 'StartingInstances[0].CurrentState.Name' --output text
	aws ec2 wait instance-running --region $(AWS_REGION) --instance-ids $$($(TF) output -raw instance_id)
	@id=$$($(TF) output -raw instance_id); for i in $$(seq 1 30); do \
	  st=$$(aws ssm describe-instance-information --region $(AWS_REGION) \
	    --filters Key=InstanceIds,Values=$$id --query 'InstanceInformationList[0].PingStatus' --output text); \
	  [ "$$st" = Online ] && { echo "SSM agent online (new public IP does not matter: SSM targets $$id)"; exit 0; }; \
	  sleep 10; done; echo "SSM agent not online after 5 min" >&2; exit 1

aws-destroy:             ## empty the bucket and ECR, then terraform destroy EVERYTHING (asks first)
	@$(AWS_GUARD) $@
	@echo "Back up results first: aws s3 sync s3://$$($(TF) output -raw bucket_name) ./aws-backup"
	@read -r -p "Delete ALL Skypiea AWS resources, bucket contents and images? Type 'destroy skypiea': " a; \
	  [ "$$a" = "destroy skypiea" ] || { echo aborted; exit 1; }
	infra/aws/scripts/empty-for-destroy.sh
	$(TF) destroy -input=false -auto-approve
	@echo "Check: no resource tagged project=skypiea left in $(AWS_REGION):"
	@aws resourcegroupstaggingapi get-resources --region $(AWS_REGION) \
	  --tag-filters Key=project,Values=skypiea --query 'ResourceTagMappingList[].ResourceARN' --output text \
	  | sed -E 's/[0-9]{12}/<account>/g' || true
	@rm -f $(AWS_KUBECONFIG)

# ---------------- checks ----------------
infra-check:             ## lint, tests, sizes, and no secrets tracked
	$(PY) -m ruff check .
	$(PY) -m pytest -q
	$(PY) tools/check_sizes.py
	@if git ls-files | grep -E 'tfstate|kubeconfig|\.env|\.pem|tfvars'; then \
	  echo "secret-like files are tracked!"; exit 1; else echo "no secret-like files tracked"; fi

e0-kind-parallel:        ## E0 with 3 tools scaled up at the same moment (n=10)
	$(PY) -m experiments.e0_cold_start --target kind --n 10 --parallel 3

e0-aws-parallel:
	@$(AWS_GUARD) $@
	$(PY) -m experiments.e0_cold_start --target aws --n 10 --parallel 3 \
	  --kubeconfig $(AWS_KUBECONFIG)

tool-map-coverage:       ## traffic share per server per scenario (needs data/processed)
	$(PY) tools/tool_map_coverage.py

kind-integration:        ## replay Scenario A and B through the gateway under each policy (functional)
	$(PY) -m gateway.replay_check --context $(KIND_CTX) --namespace $(NS)

grafana-kind:            ## open Grafana on http://127.0.0.1:3000 (anonymous viewer)
	kubectl --context $(KIND_CTX) -n $(NS) port-forward svc/skypiea-grafana 3000:3000

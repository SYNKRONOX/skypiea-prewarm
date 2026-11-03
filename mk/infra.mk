# Skypiea infra targets: tool images, kind, AWS (Terraform + k3s + ECR), E0.
# Included from the root Makefile:   include mk/infra.mk
.PHONY: build-images measure-startup infra-test cluster-kind cluster-kind-down push-images-kind \
        deploy-kind e0-kind aws-plan aws-apply aws-kubeconfig push-images-aws \
        deploy-aws e0-aws aws-stop aws-start aws-destroy infra-check aws-tunnel aws-shell \
        aws-reminder aws-gateway e0-kind-parallel e0-aws-parallel tool-map-coverage \
        deploy-kind-e0 kind-integration grafana-kind

PY          ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
IMAGE_TAG   ?= $(shell tools/image_tag.sh)
TOOL_LIST    = $(shell $(PY) -c 'import yaml;print(" ".join(yaml.safe_load(open("tools/tool_map.yaml"))["servers"]))')
IMAGES       = $(TOOL_LIST) e0-probe platform
CHART        = charts/skypiea
NS           = skypiea-tools
KIND_CTX    ?= kind-skypiea
KIND_REG    ?= localhost:5001
N           ?= 10
# AWS deploy: E0 first (no controller); afterwards PLATFORM=true E0PROBE=false
PLATFORM    ?= false
E0PROBE     ?= true
TF           = terraform -chdir=infra/aws
AWS_KUBECONFIG ?= $(HOME)/.kube/skypiea-aws
AWS_REGION  ?= ap-south-1
REMIND_H    ?= 8

# ---------------- images ----------------
build-images:            ## build all tool images + E0 probe, one at a time
	tools/build_images.sh

measure-startup:         ## image size + startup time per tool (plain docker)
	$(PY) tools/measure_startup.py --n 5

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
	  --set imageTag=$(IMAGE_TAG) --set platform.enabled=true --set e0Probe.enabled=false --wait
	kubectl --context $(KIND_CTX) -n $(NS) get deploy

deploy-kind-e0: push-images-kind  ## tools + E0 probe, NO controller (E0 scales tools itself)
	helm upgrade --install skypiea $(CHART) --kube-context $(KIND_CTX) \
	  -n $(NS) --create-namespace -f $(CHART)/values-kind.yaml \
	  --set imageTag=$(IMAGE_TAG) --set platform.enabled=false --set monitoring.enabled=false \
	  --set e0Probe.enabled=true --wait
	kubectl --context $(KIND_CTX) -n $(NS) get deploy

e0-kind:
	$(PY) -m experiments.e0_cold_start --target kind --n $(N) --modes cached,uncached --tools all

# ---------------- AWS ----------------
# No SSH and no inbound port: shell, k3s API and the gateway demo all go through SSM Session
# Manager (needs session-manager-plugin on the laptop). Run `make aws-tunnel` in its own
# terminal before anything that uses kubectl/helm (deploy-aws, e0-aws, aws-gateway, skypiea CLI).
aws-plan:                ## terraform init/validate/fmt-check + plan -> infra/aws/tfplan (never committed)
	$(TF) init -input=false
	$(TF) validate
	$(TF) fmt -check -recursive
	$(TF) plan -input=false -out=tfplan

aws-apply:               ## apply the saved, reviewed plan exactly; then write the destroy reminder
	$(TF) apply -input=false tfplan
	@rm -f infra/aws/tfplan
	@infra/aws/scripts/destroy-reminder.sh $(REMIND_H)

aws-reminder:            ## calendar file reminding you to destroy, REMIND_H hours from now (default 8)
	@infra/aws/scripts/destroy-reminder.sh $(REMIND_H)

aws-tunnel:              ## SSM port-forward 127.0.0.1:16443 -> k3s API (foreground; own terminal)
	infra/aws/scripts/tunnel.sh

aws-shell:               ## interactive root-capable shell on the VM over SSM (no SSH)
	aws ssm start-session --region $(AWS_REGION) --target $$($(TF) output -raw instance_id)

aws-kubeconfig:          ## fetch kubeconfig over SSM -> ~/.kube/skypiea-aws (outside the repo)
	KUBECONFIG_OUT=$(AWS_KUBECONFIG) infra/aws/scripts/get-k3s-config.sh

push-images-aws:         ## docker login to ECR (token via the CLI), push all images, docker logout
	@REG=$$($(TF) output -raw ecr_registry); \
	aws ecr get-login-password --region $(AWS_REGION) | \
	  docker login --username AWS --password-stdin $$REG >/dev/null && echo "logged in to $$REG"; \
	for i in $(IMAGES); do \
	  docker tag skypiea/$$i:$(IMAGE_TAG) $$REG/skypiea/$$i:$(IMAGE_TAG) && \
	  docker push -q $$REG/skypiea/$$i:$(IMAGE_TAG) >/dev/null && echo "pushed $$i" || exit 1; \
	done; \
	docker logout $$REG >/dev/null

deploy-aws:              ## helm install the chart on k3s (all tools at 0); needs aws-tunnel
	helm upgrade --install skypiea $(CHART) --kubeconfig $(AWS_KUBECONFIG) \
	  -n $(NS) --create-namespace -f $(CHART)/values-aws.yaml \
	  --set registry=$$($(TF) output -raw ecr_registry)/ \
	  --set imageTag=$(IMAGE_TAG) --set platform.enabled=$(PLATFORM) \
	  --set monitoring.enabled=$(PLATFORM) --set e0Probe.enabled=$(E0PROBE) --wait
	kubectl --kubeconfig $(AWS_KUBECONFIG) -n $(NS) get deploy

e0-aws:                  ## E0 on AWS (needs aws-tunnel; image removal goes over SSM)
	$(PY) -m experiments.e0_cold_start --target aws --n $(N) --modes cached,uncached \
	  --tools all --kubeconfig $(AWS_KUBECONFIG)

aws-gateway:             ## demo: gateway on http://127.0.0.1:8080/mcp (laptop loopback only); needs aws-tunnel
	kubectl --kubeconfig $(AWS_KUBECONFIG) -n $(NS) port-forward --address 127.0.0.1 svc/skypiea-gateway 8080:80

aws-stop:                ## stop the VM (keeps disk, ECR, S3; EBS storage still billed)
	aws ec2 stop-instances --region $(AWS_REGION) --instance-ids $$($(TF) output -raw instance_id) \
	  --query 'StoppingInstances[0].CurrentState.Name' --output text

aws-start:               ## start the VM again (kubeconfig is unchanged: it points at the tunnel)
	aws ec2 start-instances --region $(AWS_REGION) --instance-ids $$($(TF) output -raw instance_id) \
	  --query 'StartingInstances[0].CurrentState.Name' --output text
	aws ec2 wait instance-running --region $(AWS_REGION) --instance-ids $$($(TF) output -raw instance_id)

aws-destroy:             ## empty the bucket and ECR, then terraform destroy EVERYTHING (asks first)
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
	$(PY) -m experiments.e0_cold_start --target aws --n 10 --parallel 3 \
	  --kubeconfig $(AWS_KUBECONFIG)

tool-map-coverage:       ## traffic share per server per scenario (needs data/processed)
	$(PY) tools/tool_map_coverage.py

kind-integration:        ## replay Scenario A and B through the gateway under each policy (functional)
	$(PY) -m gateway.replay_check --context $(KIND_CTX) --namespace $(NS)

grafana-kind:            ## open Grafana on http://127.0.0.1:3000 (anonymous viewer)
	kubectl --context $(KIND_CTX) -n $(NS) port-forward svc/skypiea-grafana 3000:3000

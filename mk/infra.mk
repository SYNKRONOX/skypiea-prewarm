# Skypiea infra targets: tool images, kind, AWS (Terraform + k3s + ECR), E0.
# Included from the root Makefile:   include mk/infra.mk
.PHONY: build-images measure-startup infra-test cluster-kind cluster-kind-down push-images-kind \
        deploy-kind e0-kind aws-ip aws-plan aws-apply aws-up aws-kubeconfig push-images-aws \
        deploy-aws e0-aws aws-stop aws-start aws-destroy infra-check \
        e0-kind-parallel e0-aws-parallel tool-map-coverage deploy-kind-e0 kind-integration grafana-kind aws-extend

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
SSH_KEY     ?= $(HOME)/.ssh/skypiea-aws

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
aws-ip:                  ## show the public IP that would be allowed in the security group
	@infra/aws/scripts/my-ip.sh

$(SSH_KEY):
	ssh-keygen -t ed25519 -N '' -C skypiea-aws -f $(SSH_KEY)

aws-plan: $(SSH_KEY)     ## terraform plan -> infra/aws/tfplan (MY_IP=a.b.c.d required)
	@test -n "$(MY_IP)" || { echo "set MY_IP=<your public IP> (see: make aws-ip)"; exit 1; }
	$(TF) init -input=false
	$(TF) plan -input=false -var my_ip_cidr=$(MY_IP)/32 \
	  -var ssh_public_key_path=$(SSH_KEY).pub -out tfplan

aws-apply:               ## apply the saved plan exactly as reviewed
	$(TF) apply -input=false tfplan
	@rm -f infra/aws/tfplan

aws-up: aws-plan         ## plan, then terraform asks for confirmation before applying
	$(TF) apply tfplan
	@rm -f infra/aws/tfplan

aws-kubeconfig:          ## fetch kubeconfig over SSH -> ~/.kube/skypiea-aws (not in the repo)
	SSH_KEY=$(SSH_KEY) KUBECONFIG_OUT=$(AWS_KUBECONFIG) infra/aws/scripts/get-k3s-config.sh

push-images-aws:         ## docker login to ECR, push all images, docker logout
	@REG=$$($(TF) output -raw ecr_registry); \
	aws ecr get-login-password --region $(AWS_REGION) | \
	  docker login --username AWS --password-stdin $$REG >/dev/null && echo "logged in to $$REG"; \
	for i in $(IMAGES); do \
	  docker tag skypiea/$$i:$(IMAGE_TAG) $$REG/skypiea/$$i:$(IMAGE_TAG) && \
	  docker push -q $$REG/skypiea/$$i:$(IMAGE_TAG) >/dev/null && echo "pushed $$i" || exit 1; \
	done; \
	docker logout $$REG >/dev/null

deploy-aws:              ## helm install the tools chart on the k3s VM (all tools at 0)
	helm upgrade --install skypiea $(CHART) --kubeconfig $(AWS_KUBECONFIG) \
	  -n $(NS) --create-namespace -f $(CHART)/values-aws.yaml \
	  --set registry=$$($(TF) output -raw ecr_registry)/ \
	  --set imageTag=$(IMAGE_TAG) --set platform.enabled=$(PLATFORM) \
	  --set monitoring.enabled=$(PLATFORM) --set e0Probe.enabled=$(E0PROBE) --wait
	kubectl --kubeconfig $(AWS_KUBECONFIG) -n $(NS) get deploy

e0-aws:
	$(PY) -m experiments.e0_cold_start --target aws --n $(N) --modes cached,uncached \
	  --tools all --kubeconfig $(AWS_KUBECONFIG) --ssh-key $(SSH_KEY)

aws-stop:                ## stop the VM (keeps disk, ECR, S3; EBS storage still billed)
	aws ec2 stop-instances --region $(AWS_REGION) --instance-ids $$($(TF) output -raw instance_id) \
	  --query 'StoppingInstances[0].CurrentState.Name' --output text

aws-start:               ## start the VM and refresh the kubeconfig (public IP changes)
	aws ec2 start-instances --region $(AWS_REGION) --instance-ids $$($(TF) output -raw instance_id) \
	  --query 'StartingInstances[0].CurrentState.Name' --output text
	aws ec2 wait instance-running --region $(AWS_REGION) --instance-ids $$($(TF) output -raw instance_id)
	$(TF) apply -refresh-only -auto-approve -input=false -var my_ip_cidr=$$(infra/aws/scripts/my-ip.sh)/32 \
	  -var ssh_public_key_path=$(SSH_KEY).pub >/dev/null
	$(MAKE) aws-kubeconfig

aws-destroy:             ## destroy EVERYTHING in infra/aws (asks for confirmation)
	@read -r -p "Destroy all Skypiea AWS resources (VM, ECR images, bucket)? Type 'destroy skypiea': " a; \
	  [ "$$a" = "destroy skypiea" ] || { echo aborted; exit 1; }
	$(TF) destroy -input=false -auto-approve -var my_ip_cidr=$$(infra/aws/scripts/my-ip.sh)/32 \
	  -var ssh_public_key_path=$(SSH_KEY).pub

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
	  --kubeconfig $(AWS_KUBECONFIG) --ssh-key $(SSH_KEY)

tool-map-coverage:       ## traffic share per server per scenario (needs data/processed)
	$(PY) tools/tool_map_coverage.py

kind-integration:        ## replay Scenario A and B through the gateway under each policy (functional)
	$(PY) -m gateway.replay_check --context $(KIND_CTX) --namespace $(NS)

grafana-kind:            ## open Grafana on http://127.0.0.1:3000 (anonymous viewer)
	kubectl --context $(KIND_CTX) -n $(NS) port-forward svc/skypiea-grafana 3000:3000

aws-extend:              ## raise the VM's max-uptime cost guard to H hours (no instance replacement)
	@test -n "$(H)" || { echo "usage: make aws-extend H=<hours>"; exit 1; }
	ssh -o BatchMode=yes -i $(SSH_KEY) ubuntu@$$($(TF) output -raw instance_ip) \
	  "echo $(H) | sudo tee /etc/skypiea/max-uptime-hours >/dev/null && uptime -p"

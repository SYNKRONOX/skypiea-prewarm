# AWS cloud run (Terraform + k3s + ECR)

One EC2 VM in **ap-south-1** running single-node [k3s](https://k3s.io/), with the same Helm
chart as the local kind cluster (`charts/skypiea-tools`, `values-aws.yaml`). Everything is
tagged `project=skypiea`. State is **local** (`infra/aws/terraform.tfstate`, git-ignored).

## What gets created

| Resource | Details |
|---|---|
| EC2 instance `skypiea-node` | `m7i-flex.large` (2 vCPU, 8 GiB), Ubuntu 24.04 LTS, 30 GB gp3 encrypted root, IMDSv2 only |
| Security group `skypiea-node` | Inbound **22** and **6443** from your IP (`/32`) only. Nothing is open to `0.0.0.0/0`. Outbound all |
| Key pair `skypiea-aws` | From `~/.ssh/skypiea-aws.pub` (created by `make aws-plan` if missing) |
| IAM role + instance profile `skypiea-node` | `AmazonEC2ContainerRegistryReadOnly` + read/write on the one bucket only |
| ECR repositories `skypiea/<tool>` | One per tool in `tools/tool_map.yaml` + `skypiea/e0-probe`. Lifecycle: keep the last 3 images |
| S3 bucket `skypiea-<random>` | Models and results. All public access blocked, versioning on, SSE-S3 |

`user_data` installs k3s (pinned `v1.37.1+k3s1`, traefik/servicelb/metrics-server disabled)
and a systemd timer `skypiea-ecr-refresh.timer` that re-creates the `skypiea-tools/ecr-pull`
image pull secret every 6 h (ECR tokens expire after 12 h). The token comes from the instance
role; no AWS keys are ever stored on the VM, in the repo or in Terraform state.

## Usage

```bash
aws sts get-caller-identity                  # must work first
aws ec2 describe-instance-types --region ap-south-1 \
  --filters Name=free-tier-eligible,Values=true --query 'InstanceTypes[].InstanceType'
make aws-ip                                  # your public IP; check it
make aws-plan MY_IP=<a.b.c.d>                # review the plan
make aws-apply                               # apply exactly that plan
make build-images push-images-aws            # build + push to ECR
make aws-kubeconfig                          # -> ~/.kube/skypiea-aws (outside the repo)
make deploy-aws                              # helm install, all tools at 0 replicas
make e0-aws N=30                             # E0 on AWS
```

Always pass the kubeconfig explicitly: `kubectl --kubeconfig ~/.kube/skypiea-aws ...`.
The kubeconfig sets `tls-server-name: kubernetes` (a SAN that is always in the k3s cert), so it
keeps working when the public IP changes after a stop/start; `make aws-start` rewrites the IP.
If **your** IP changes, re-run `make aws-plan MY_IP=<new> && make aws-apply` to update the
security group.

## Cost notes (check the AWS console; prices change)

- `m7i-flex.large` is free-tier eligible on newer accounts (credit-based free plan). We confirm
  that with `describe-instance-types` before creating anything. Outside the free tier it is
  billed per second while **running**.
- The 30 GB gp3 volume is billed while it exists, **including while the VM is stopped**.
- The public IPv4 address is billed per hour while the instance is running.
- ECR storage (about 15 images × ≤3 versions) and S3 are cents per month at this size. Data
  transfer from ECR to EC2 in the same region is free.

## Stop, start, destroy

```bash
make aws-stop       # stop the VM at the end of every day (disk, ECR, S3 kept)
make aws-start      # start it again and refresh ~/.kube/skypiea-aws
make aws-destroy    # deletes everything; asks you to type "destroy skypiea"
```

The bucket is **not** force-deleted by default: if it holds objects, `aws-destroy` stops at the
bucket. Download what you need first (`aws s3 sync s3://<bucket> ./backup`), then empty it or
run with `-var bucket_force_destroy=true` once.

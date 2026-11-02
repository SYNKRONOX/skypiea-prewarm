# AWS plan review (before `terraform plan`)

What `infra/aws/` will create, what it costs, how it is locked down and how to tear it down.
Written from the Terraform code; **`terraform plan` / `apply` have not been run** (no
credentials yet). Run `make aws-plan MY_IP=<your IP>` and compare its resource list with the
table below before answering "Apply?".

## Resources: 53 in ap-south-1 (Mumbai), all tagged `project=skypiea`

| # | Terraform address | What |
|---|---|---|
| 1 | `aws_instance.node` | EC2 `m7i-flex.large` (2 vCPU, 8 GiB), Ubuntu 24.04 LTS (Canonical AMI, latest), 30 GB gp3 encrypted root, IMDSv2 only (hop limit 1), shutdown -> **stop** |
| 1 | `aws_security_group.node` | Security group `skypiea-node` in the default VPC |
| 2 | `aws_vpc_security_group_ingress_rule.ssh`, `.k3s_api` | TCP 22 and TCP 6443 from **your IP /32 only** |
| 2 | `aws_vpc_security_group_egress_rule.https`, `.http` | TCP 443 and TCP 80 out (see below) |
| 1 | `aws_key_pair.op` | Public key from `~/.ssh/skypiea-aws.pub` (private key never leaves the laptop) |
| 4 | `aws_iam_role.node`, `aws_iam_role_policy_attachment.ecr_ro`, `aws_iam_role_policy.bucket_rw`, `aws_iam_instance_profile.node` | Instance role: `AmazonEC2ContainerRegistryReadOnly` + read/write on the one bucket only |
| 18 | `aws_ecr_repository.img["<name>"]` | `skypiea/<tool>` for the 16 tools (from `tools/tool_map.yaml`) + `skypiea/e0-probe` + `skypiea/platform`; `force_delete` (images are rebuildable) |
| 18 | `aws_ecr_lifecycle_policy.keep3["<name>"]` | Keep only the last 3 images per repository |
| 1 | `random_id.bucket` | Suffix for the bucket name |
| 4 | `aws_s3_bucket.data` + public access block + versioning + SSE-S3 | `skypiea-<random>` for models and results; all public access blocked |
| 1 | `aws_cloudwatch_metric_alarm.idle_stop` | **Cost guard**: stops the VM after 60 min below 5% CPU |

Data sources only (nothing created): caller identity, default VPC, Ubuntu AMI lookup, IAM
policy documents. State is local (`infra/aws/terraform.tfstate`, git-ignored).

## Security groups

| Direction | Port | Source / destination | Why |
|---|---|---|---|
| in | 22/tcp | **your IP /32** | SSH: kubeconfig fetch, `crictl rmi` for E0, `aws-extend` |
| in | 6443/tcp | **your IP /32** | k3s API for kubectl / helm / the `skypiea` CLI |
| out | 443/tcp | 0.0.0.0/0 | **Unavoidable**: ECR, S3 and the AWS APIs (ECR token refresh) are HTTPS on public AWS addresses; the k3s installer (get.k3s.io, GitHub releases) and snap (aws-cli) are HTTPS on the internet |
| out | 80/tcp | 0.0.0.0/0 | **Unavoidable for apt**: Ubuntu's mirrors are HTTP (packages are signature-checked) |

Nothing is open to 0.0.0.0/0 inbound. No other ports are open in either direction: DNS (VPC
resolver) and time sync (169.254.169.123) are link-local and not filtered by security groups.
Everything inside k3s (gateway, controller, Grafana, Prometheus) is ClusterIP and reachable
only through the k3s API (kubectl port-forward / API proxy) from your IP.
The `make aws-plan` step refuses to run without `MY_IP`, and the variable validation rejects
anything that is not a single `/32`.

Possible tightening, not done (costs money): VPC interface endpoints for ECR/S3 would remove
most of the 443 egress, but each interface endpoint is billed per hour per AZ.

## Cost (USD, on-demand list prices for ap-south-1)

Prices are from AWS's public price lists, fetched 2026-11-02 (publication dates in brackets);
**check the console before applying**, and whether this account's free plan covers the
instance (the `describe-instance-types --filters Name=free-tier-eligible,Values=true` check in
`infra/aws/README.md` must pass first).

| Item | Price | Source (publication date) |
|---|---|---|
| m7i-flex.large, Linux | $0.10075 per hour | EC2 on-demand, Asia Pacific (Mumbai) (2026-10-29) |
| Public IPv4 address (in use) | $0.005 per hour | VPC, Mumbai (2026-10-16) |
| gp3 storage | $0.0912 per GB-month | EBS, Mumbai (2026-10-29) |
| ECR storage | $0.10 per GB-month | ECR, Mumbai (2026-09-11) |
| S3 Standard storage | $0.025 per GB-month (first 50 TB) | S3, Mumbai (2026-10-15) |
| CloudWatch standard alarm | $0.10 per alarm-month | CloudWatch, Mumbai (2026-10-07) |

Derived from those prices (arithmetic, not a bill):

| State | Cost |
|---|---|
| **Running** | $0.10075 + $0.005 + 30 GB x $0.0912 / 730 h = **about $0.110 per hour** (about $2.63 per 24 h) |
| **Stopped** (`make aws-stop`) | instance and IPv4 stop billing; the 30 GB volume stays: 30 x $0.0912 = **$2.74 per month** |
| ECR | images are about 0.05-0.15 GB each compressed (kind registry: light 63 MB, heavy 145-149 MB); 18 repos x up to 3 tags -> a few GB at $0.10/GB-month: **well under $1 per month** |
| S3, alarm | cents per month (results are MB-scale); the alarm is $0.10/month unless covered by the free tier |
| Data transfer | ECR -> EC2 in the same region is free; outbound to the laptop is kubectl traffic only |

Biggest risk: forgetting a running VM (about $79 per 30 days at the running rate). The cost
guards below exist for that.

## Cost guards

1. **Idle auto-stop** (`aws_cloudwatch_metric_alarm.idle_stop`): average CPU below
   `idle_stop_cpu_percent` (5%) for `idle_stop_minutes` (60) -> EC2 **stop** action. Uses free
   basic monitoring (5-minute datapoints). k3s itself uses some CPU when idle: check the idle
   baseline in CloudWatch after the first boot and adjust the threshold if the VM never
   reaches it.
2. **Max uptime** (systemd timer on the VM, every 5 min): the VM powers itself off after
   `max_uptime_hours` (12 h) of uptime; `instance_initiated_shutdown_behavior = "stop"` makes
   that a stop, never a termination. For a long run (E0 at n=100 may take longer):
   `make aws-extend H=24` raises it on the VM without replacing the instance.
3. **Manual**: `make aws-stop` at the end of every day; `make aws-start` refreshes the
   kubeconfig (the public IP changes).

A stopped VM keeps its disk, ECR images and S3 data; only storage is billed.

## Teardown

```bash
make aws-stop                       # pause (storage still billed)
aws s3 sync s3://$(terraform -chdir=infra/aws output -raw bucket_name) ./aws-backup  # keep results
make aws-destroy                    # asks you to type "destroy skypiea"; removes all 53 resources
```

- ECR repositories have `force_delete`, so images go with them.
- The bucket is **not** force-deleted by default: if it still holds objects, destroy stops at
  the bucket. Empty it (after the backup) or run once with `-var bucket_force_destroy=true`.
- Afterwards check the console (EC2, EBS volumes, Elastic IPs: none are created, ECR, S3,
  CloudWatch alarms) in ap-south-1 for anything tagged `project=skypiea`, and delete
  `~/.kube/skypiea-aws`.

## Before answering "Apply?"

- [ ] `aws sts get-caller-identity` works (the right account).
- [ ] The free-tier check for `m7i-flex.large` passes (or the cost above is accepted).
- [ ] `make aws-ip` shows your current public IP; the plan's two ingress rules use it as `/32`.
- [ ] The plan lists 53 resources to add, 0 to change, 0 to destroy.
- [ ] No ingress rule with `0.0.0.0/0`; egress only 443 and 80.

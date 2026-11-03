# AWS plan (reviewed `terraform plan`, not applied)

What `infra/aws/` creates, what it costs, how it is locked down, how images, E0 and the
controller run on it, and how to tear it down. Everything here comes from the saved plan,
`terraform plan -out=tfplan` run on 2026-11-03 (ap-south-1, provider hashicorp/aws 6.x). The
plan file stays in `infra/aws/tfplan` and is git-ignored. **Nothing has been applied.**

```
terraform init      -> ok
terraform validate  -> Success! The configuration is valid.
terraform fmt -check -recursive -> no changes
terraform plan -out=tfplan -> Plan: 50 to add, 0 to change, 0 to destroy.
```

Reproduce with `make aws-plan` (no variables are needed: there is no IP to pass, because
nothing is open inbound).

## Resources the plan creates: 50 (49 AWS + 1 local random suffix)

| Count | Type | Address / name | Notes |
|---:|---|---|---|
| 1 | `aws_instance` | `node` = `skypiea-node` | `m7i-flex.large` (2 vCPU, 8 GiB), Ubuntu 24.04 (`ubuntu-noble-24.04-amd64-server-20261023`, Canonical), IMDSv2 required (hop limit 1), 20 GB gp3 root **encrypted**, public IPv4 for outbound only, **no key pair** |
| 1 | `aws_security_group` | `node` = `skypiea-node` | default VPC; **no ingress rules** |
| 1 | `aws_vpc_security_group_egress_rule` | `https` | TCP 443 out (see below) |
| 1 | `aws_iam_role` | `node` = `skypiea-node` | EC2 assume-role only |
| 3 | `aws_iam_role_policy` | `ecr_pull`, `bucket_rw`, `ssm_session` | inline, least privilege (below) |
| 1 | `aws_iam_instance_profile` | `node` = `skypiea-node` | |
| 18 | `aws_ecr_repository` | `img["<name>"]` = `skypiea/<name>` | 16 tools from `tools/tool_map.yaml` + `e0-probe` + `platform`; AES256; `force_delete` |
| 18 | `aws_ecr_lifecycle_policy` | `keep3["<name>"]` | keep the last 3 images per repository |
| 1 | `aws_s3_bucket` | `data` = `skypiea-<8 hex>` | models and results; `force_destroy = false` |
| 1 | `aws_s3_bucket_public_access_block` | `data` | all four blocks on |
| 1 | `aws_s3_bucket_ownership_controls` | `data` | `BucketOwnerEnforced` (ACLs disabled) |
| 1 | `aws_s3_bucket_server_side_encryption_configuration` | `data` | SSE-S3 (AES256) |
| 1 | `aws_s3_bucket_policy` | `data` | deny any request without TLS |
| 1 | `random_id` | `bucket` | bucket name suffix (local, not an AWS resource) |

Also read, not created: the caller identity, the default VPC, the Ubuntu AMI lookup and 3 IAM
policy documents. State is local (`infra/aws/terraform.tfstate`, git-ignored).

**Tags.** The provider's `default_tags` puts `project=skypiea` on everything that accepts
tags. The plan check confirms all 24 taggable resources have it in `tags_all`; the root volume
gets it explicitly. Not taggable in AWS, so untagged: ECR lifecycle policies, IAM inline
policies, the S3 bucket sub-resources (public access block, ownership, encryption, policy) and
`random_id`. The instance's network interface is created by EC2, not Terraform, and has no
tags.

**Compared with the previous draft**, removed: the SSH key pair, both inbound rules (22 and
6443), the HTTP-80 egress rule, the CloudWatch idle-stop alarm, the max-uptime power-off and
S3 versioning. Replaced: the managed policy `AmazonEC2ContainerRegistryReadOnly` with a policy
scoped to our repositories. Added: SSM, the bucket TLS policy and ownership controls.

## Security group `skypiea-node`

| Direction | Protocol/port | Peer | Why |
|---|---|---|---|
| inbound | none | none | No inbound rule at all. Shell, the k3s API and the gateway demo go through SSM Session Manager. The SSM agent connects **out** to AWS over HTTPS. |
| outbound | TCP 443 | `0.0.0.0/0` | SSM and ssmmessages; the ECR API, with layer downloads served from S3; ECR token refresh; get.k3s.io and GitHub releases (the k3s install); snapcraft (aws-cli). All are public HTTPS endpoints. |

DNS (the VPC resolver) and time sync (169.254.169.123) are link-local, and security groups
don't filter them. Terraform removes the "allow all" egress rule that AWS adds to a new
security group, so 443 is the only path out. Nothing on the VM uses apt, so port 80 is closed.

**The one deliberate exception: outbound TCP 443 to `0.0.0.0/0`** (accepted 2026-11-03).
It is outbound only, and nothing can connect in. It is needed because the k3s install
(get.k3s.io, GitHub releases), ECR (API and S3-hosted layers) and SSM are public HTTPS
endpoints with no fixed address list. Every other rule follows "nothing open to 0.0.0.0/0".
The alternative, not taken, would need:
- 4 VPC interface endpoints (`ecr.api`, `ecr.dkr`, `ssm`, `ssmmessages`), each billed at
  **$0.013 per hour** in Mumbai (VPC price list, 2026-11-03), which adds $0.052/h, about
  +48%;
- the free S3 gateway endpoint, for the ECR layers;
- an air-gapped k3s install: binary, images and the ECR credential helper staged in our
  bucket from the laptop.

Not taken: it adds cost and an offline install path for no gain in inbound exposure.

**Reaching the gateway for the demo.** The gateway, Grafana and Prometheus are ClusterIP
Services, with no NodePort and no LoadBalancer. `make aws-tunnel` opens an SSM port-forward
from laptop `127.0.0.1:16443` to the VM's k3s API on 6443. `make aws-gateway` then runs
`kubectl port-forward --address 127.0.0.1` over it, which gives `http://127.0.0.1:8080/mcp`.
It listens on your laptop's loopback only, and nothing is public.

## IAM

### Instance role `skypiea-node` (what the VM can do)

| Policy | Actions | Resources |
|---|---|---|
| `skypiea-ecr-pull` | `ecr:GetAuthorizationToken` | `*` (an account-level API that AWS doesn't scope to a repository) |
| | `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer`, `ecr:BatchCheckLayerAvailability` | the 18 `skypiea/*` repository ARNs only (pull, no push) |
| `skypiea-bucket-rw` | `s3:ListBucket`, `s3:GetBucketLocation` | our bucket only |
| | `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` | `our-bucket/*` only |
| `skypiea-ssm-session` | `ssm:UpdateInstanceInformation`, `ssmmessages:CreateControlChannel`, `ssmmessages:CreateDataChannel`, `ssmmessages:OpenControlChannel`, `ssmmessages:OpenDataChannel` | `*` (the agent APIs have no resource-level scoping) |

The SSM policy is the minimal one from the Session Manager documentation ("Create a custom IAM
role for Session Manager"). It replaces `AmazonSSMManagedInstanceCore`, which would also allow
Run Command, Parameter Store reads and inventory. Session logging to S3 or CloudWatch and KMS
aren't used, so those statements are left out.

IMDSv2 is required with hop limit 1, so pods can't fetch the role credentials. Only processes
on the host can: the SSM agent and the ECR token refresher.

### Your user `skypiea-terraform` (what you need to add)

`infra/aws/operator-policy.json` is a ready-to-attach **customer-managed** policy. It is about
5.7 KB, which is over the 2,048-character limit for an inline user policy. It is scoped to
`skypiea-*` names, ap-south-1, `project=skypiea` tags and `m7i-flex.large`. It covers:

| For | Actions |
|---|---|
| plan / describe | `ec2:Describe*`, `ssm:DescribeInstanceInformation`, `ssm:DescribeSessions`, `tag:GetResources`, `pricing:GetProducts` (optional) |
| VM | `ec2:RunInstances` (only `m7i-flex.large`, ap-south-1), `ec2:CreateSecurityGroup`, `ec2:AuthorizeSecurityGroupEgress`, `ec2:RevokeSecurityGroupEgress`, `ec2:CreateTags`; on `project=skypiea` resources only: `ec2:TerminateInstances`, `ec2:StopInstances`, `ec2:StartInstances`, `ec2:ModifyInstanceAttribute`, `ec2:DeleteSecurityGroup`, `ec2:ModifySecurityGroupRules`, `ec2:DeleteTags` |
| IAM | create, read, tag and delete role and instance profile `skypiea-node`, `iam:PutRolePolicy`, `iam:GetRolePolicy`, `iam:DeleteRolePolicy`, `iam:AddRoleToInstanceProfile`, `iam:RemoveRoleFromInstanceProfile`; `iam:PassRole` on `skypiea-node` to `ec2.amazonaws.com` only |
| ECR | `ecr:GetAuthorizationToken`; on `skypiea/*`: create, describe, tag and delete repositories, lifecycle policy, push (`ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`, `ecr:PutImage`, `ecr:BatchCheckLayerAvailability`), pull, and list and delete images |
| S3 | on `skypiea-*`: create and delete bucket, `s3:GetBucket*`, put bucket tagging, policy, public access block, ownership and encryption, object get, put and delete, list versions, `s3:DeleteObjectVersion` |
| SSM | `ssm:StartSession` on instances tagged `project=skypiea` with the documents `AWS-StartPortForwardingSession`, `AWS-StartNonInteractiveCommand` and `SSM-SessionManagerRunShell`; `ssm:TerminateSession` and `ssm:ResumeSession` on your own sessions |

What I could see: `sts:GetCallerIdentity`, the plan's reads (`ec2:DescribeImages`,
`ec2:DescribeVpcs`) and `ec2:DescribeInstanceTypes` work. `iam:List*`, `ssm:DescribeDocument`
and `pricing:GetProducts` are denied. I can't list your current permissions, so apply may still
need some of the create permissions above.

## Root volume: 20 GB, from measured image sizes

These were measured on 2026-11-03 from the images in the local registry (tag `a8567cc`) and on
the kind nodes. On a node, containerd stores the compressed layers *and* the unpacked
snapshots.

| What | Measured | On disk |
|---|---|---|
| 18 Skypiea images: per-image compressed 52-149 MB, unpacked 265-634 MB (light 265, medium 470, heavy 625-634, probe 316, platform 400) | sum 1.56 GB compressed / 6.86 GB unpacked; **deduplicated (shared base and dependency layers) 0.31 GB compressed + 1.24 GB unpacked** | 1.55 GB |
| A second image set during a rolling update (old tag still present) | same | +1.55 GB |
| Prometheus + Grafana (crictl on the kind worker: 114 MB + 503 MB) | x2 for compressed + unpacked, upper bound | 1.3 GB |
| k3s system images (pause, coredns, local-path), k3s binary and data | kind worker containerd with all of the above: **3.4 GB** measured | about 0.8 GB |
| Prometheus TSDB / Grafana emptyDir limits (chart) | 2 Gi + 0.5 Gi caps | up to 2.5 GB |
| Ubuntu 24.04 base + snaps (aws-cli, SSM agent), logs | AMI default volume is 8 GB | about 3 GB |
| **Peak total** | | **about 10.7 GB** |

Kubelet image GC starts at 85% disk use. If it ran during E0 it would delete cached images and
corrupt the "cached" runs, so the disk must stay well under 85% at peak: 10.7 / 0.85 = 12.6 GB
is the minimum. **20 GB keeps the peak at about 54%**, which leaves room for a third image set
or larger results. That is less than the previous 30 GB and saves $0.91 per month. The
variable refuses less than 12 GB. After apply I will check real usage with
`make aws-shell` → `df -h /`.

## Cost (USD, on-demand list prices, ap-south-1)

These are AWS's public price lists (`b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/...`). I
fetched them again on 2026-11-03, and they match the 2026-11-02 fetch. The date in brackets is
each list's publication date. The Pricing API (`pricing:GetProducts`) is denied for this user.

| Item | Price | Source (publication date) |
|---|---|---|
| m7i-flex.large, Linux, shared | **$0.10075 per hour** | EC2 on-demand, Asia Pacific (Mumbai) (2026-10-29) |
| Public IPv4 address, in use | **$0.005 per hour** | VPC, Mumbai (2026-11-03) |
| gp3 storage | **$0.0912 per GB-month** | EBS, Mumbai (2026-10-29) |
| ECR storage | $0.10 per GB-month | ECR, Mumbai (2026-09-11) |
| S3 Standard, first 50 TB | $0.025 per GB-month | S3, Mumbai (2026-10-15) |
| (alternative only) VPC interface endpoint | $0.013 per hour each | VPC, Mumbai (2026-11-03) |

Derived from these prices (my arithmetic, not a bill):

| | Per hour | Per day (24 h) |
|---|---:|---:|
| Instance | $0.10075 | $2.418 |
| Public IPv4 | $0.005 | $0.120 |
| 20 GB gp3 (20 x 0.0912 / 730 h) | $0.0025 | $0.060 |
| **Running total** | **$0.108** | **$2.60** |
| ECR, upper bound: 1.56 GB per tag set x 3 kept tags = 4.7 GB | $0.0006 | $0.016 |
| S3 (results are MB-scale) | < $0.0001 | < $0.01 |
| **If stopped** (`make aws-stop`): instance and public IPv4 not billed | $0.0025 | $0.06 |

Not priced here:
- Internet data transfer out, which is SSM and kubectl traffic to the laptop: MB-scale.
- SSM Session Manager has no charge for EC2 instances, and ECR to EC2 in the same region is
  free.
- `m7i-flex.large` reports `FreeTierEligible: True` for this account
  (`ec2:DescribeInstanceTypes`). Whether the account's free-plan credits cover it depends on
  your plan, so the numbers above assume no credits.

### Cost guard: a reminder, not an auto-stop

An auto-stop would kill long E0 runs, so there is none. Instead:
1. **The hourly cost is above**: $0.108/h, $2.60/day, about $78 per 30 days if forgotten.
2. **Calendar reminder**: `make aws-apply` ends by writing
   `~/skypiea-aws-destroy-reminder.ics`, an event `REMIND_H` hours later (default 8) titled
   "Skypiea AWS: run make aws-destroy", with an alert at the start. Import it into your
   calendar. `make aws-reminder REMIND_H=24` writes a new one, for example before a long E0
   run. The file is written outside the repo.
3. **Overnight: `make aws-stop`** (EC2 stop, never destroy). Only the 20 GB volume stays
   billed, about $0.06/day. The cached images, the k3s state and the deployed chart stay on
   the EBS volume. **`make aws-start`** boots the VM again:
   - k3s restarts the pods, and the ECR pull secret refreshes 2 min after boot.
   - The public IPv4 **changes** on every start, because it is auto-assigned and not an
     Elastic IP.
   - **SSM does not care.** Sessions target the instance id, and the agent connects outbound.
     The kubeconfig points at the local tunnel (`127.0.0.1:16443`), so `make aws-tunnel` and
     everything else work unchanged. No IP appears anywhere in our config.
4. `make aws-destroy` ends all charges.

## How images get from the laptop to ECR

```bash
make build-images        # one at a time (the laptop is shared with training)
make push-images-aws     # aws ecr get-login-password | docker login --password-stdin <registry>
                         # docker tag + push skypiea/<image>:<tag> for all 18; docker logout
```

The ECR token comes from your CLI profile and is piped straight into `docker login`. It is
never printed or written to the repo, and `docker logout` removes it afterwards.

On the VM, a systemd timer (`skypiea-ecr-refresh`, at boot and every 6 h; tokens expire after
12 h) gets a pull token with the **instance role** and writes it into the Kubernetes Secret
`skypiea-tools/ecr-pull`, which the chart uses as `imagePullSecrets`. No AWS keys exist on the
VM, in the repo or in Terraform state.

## How E0 and the controller run on the node

The VM is a single-node k3s, version `v1.37.1+k3s1`, pinned to match kind (1.37). It is the
`v1.37`/latest channel. If it misbehaves, the fallback is the `stable` channel (v1.36.5,
`-var k3s_version=v1.36.5+k3s1`), and that switch will be reported. traefik, servicelb and metrics-server are disabled. The same
Helm chart runs as on kind (`charts/skypiea`, `values-aws.yaml`).

Laptop prerequisites:
- aws CLI v2.
- `session-manager-plugin` 1.2.896.0, installed without sudo:
  - source: `https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb`
    (4,065,612 bytes; the MD5 matches the S3 ETag `360e2daed08675dc57d9f95314f0dbb8`);
  - extracted with `dpkg-deb -x` into `~/.local/lib/sessionmanagerplugin`, linked from
    `~/.local/bin`.

```bash
make aws-apply                    # the reviewed tfplan only; writes the destroy reminder
make aws-tunnel                   # own terminal: SSM port-forward 127.0.0.1:16443 -> k3s API
make aws-kubeconfig               # kubeconfig over SSM -> ~/.kube/skypiea-aws (server 127.0.0.1:16443)
make push-images-aws
make deploy-aws                   # E0 layout: tools at 0 + e0-probe; NO controller, NO monitoring
make e0-aws N=30                  # then N=100, and make e0-aws-parallel
```

E0:
- The harness (`experiments/e0_cold_start.py --target aws`) runs on the laptop and drives
  Kubernetes through the tunnel: scale 0 to 1, watch the pod, read kubelet events.
- The timing itself is measured **inside the cluster** by the e0-probe pod, so the tunnel's
  latency does not enter the cold-start numbers.
- For uncached runs, image removal runs on the node as one `k3s crictl rmi <ids...>` per run,
  via `infra/aws/scripts/node-run.sh`. That script uses an SSM `AWS-StartNonInteractiveCommand`
  session, so there is no SSH.
- The quiet-machine check (load, memory, running containers) also goes over SSM, and runs
  before each run, never during one.
- The controller and monitoring are **off** during E0, so nothing else competes for the 2
  vCPUs.

Controller, after E0:
- `make deploy-aws PLATFORM=true E0PROBE=false` adds the controller (kopf, single replica,
  namespaced RBAC: patch Deployments and their scale in `skypiea-tools` only), the gateway,
  the predictor placeholder, Prometheus and Grafana, all as ClusterIP.
- The controller scales tool Deployments; new pods pull from ECR with `ecr-pull`.
- Demo: `make aws-gateway` serves `http://127.0.0.1:8080/mcp`. `bin/skypiea --kubeconfig
  ~/.kube/skypiea-aws status` works over the same tunnel.

Other access:
- `make aws-shell` opens an interactive SSM shell on the VM, for logs
  (`/var/log/skypiea-bootstrap.log`) and `df -h`.
- `make aws-stop` / `make aws-start` stop and start the VM (see "Cost guard" above). The
  kubeconfig doesn't change, because it points at the tunnel and not at the public IP.

Load tests: the functional Scenario A/B replay and a 20-50 concurrent-session test run on
this 2-vCPU node. **NFR-4 (500 concurrent sessions) is covered by the simulator, not by
this node.** We do not claim 500 sessions on 2 vCPUs.

## Teardown: `make aws-destroy`

1. It prints the backup command: `aws s3 sync s3://<bucket> ./aws-backup`.
2. It asks you to type `destroy skypiea`.
3. `infra/aws/scripts/empty-for-destroy.sh` empties the bucket (every object version and
   delete marker, in batches of 1000) and every `skypiea/*` ECR repository (batches of 100).
   It prints counts only.
4. `terraform destroy` removes all 50 resources.
5. It lists anything still tagged `project=skypiea` in ap-south-1 (`tag:GetResources`, account
   id masked); the list should be empty.
6. It deletes `~/.kube/skypiea-aws`.

**Terraform state.** It lives only on this laptop, in
`~/skypiea-infra/infra/aws/terraform.tfstate` (plus `terraform.tfstate.backup`). It is
git-ignored, never committed and never uploaded. It contains resource ids and the account id,
but no keys. **If it is lost**, `make aws-destroy` cannot work, and teardown has to start from
the tag listing instead:

```bash
aws resourcegroupstaggingapi get-resources --region ap-south-1 \
  --tag-filters Key=project,Values=skypiea --query 'ResourceTagMappingList[].ResourceARN'
```

Then delete each listed resource by hand: instance, security group, ECR repositories,
bucket. Then delete the IAM role and instance profile `skypiea-node` by name, because IAM is
global and `tag:GetResources` doesn't list it per region. Back the state file up (outside git)
if the laptop is at risk.

Afterwards, delete the calendar event and check the console (EC2, EBS volumes, ECR, S3, IAM
role `skypiea-node`).

## Decisions (2026-11-03)

1. Outbound 443 to `0.0.0.0/0`: accepted as the one deliberate exception (see Security group).
2. The operator policy `infra/aws/operator-policy.json` is being attached by the user as a customer-managed
   policy, replacing the broad ones; plan re-run after "policy attached".
3. The calendar reminder defaults to 8 h after apply; `make aws-stop` overnight.
4. S3 versioning stays off.
5. k3s v1.37.1 stays, to match kind; fall back to stable if it misbehaves.
6. session-manager-plugin is installed in `~/.local` (above).

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
make e0-aws-node IMAGE_TAG=a8567cc  # E0 ON the node; the tunnel can be closed
make e0-aws-status                # poll: done/total, ETA, last error (one short SSM call)
make e0-aws-fetch                 # after the job stopped the VM: verify + reports
```

E0 runs **on the node**, unattended (see the incident below):
- `make e0-aws-node` exports the committed harness (`git archive HEAD`, so the recorded commit
  is the code that ran), uploads it to S3, and starts the systemd unit `skypiea-e0`. It is a
  few short SSM calls, each capped at 60 s; no tunnel and no long `kubectl exec` from the laptop.
- The unit runs `experiments/e0_cold_start.py --target aws --on-node`: local `k3s kubectl` and
  `k3s crictl`, with the same parameters as kind. It runs sequential n=10 (cached and uncached,
  all 16 tools), then parallel-3 n=10.
- Every kubectl/crictl call has a hard timeout. A stalled or failed run is written to the CSV
  with its `error` and `attempt`. After it, the tools are scaled back to 0 and the probe is
  restarted, and the run is repeated (up to 3 attempts). Failed attempts are never dropped:
  the summary counts them as `n_failed`.
- `Restart=on-failure` plus `--resume`: a restarted unit continues from its own partial CSV.
  `results/<stem>_progress.json` is rewritten after every unit.
- At the end the job uploads the CSVs, the log and `run_params.json` to
  `s3://<bucket>/e0/<run id>/`. It checks each object's size and SHA-256 with
  `head-object --checksum-mode`, and only then runs `shutdown -h now`. An instance-initiated
  shutdown means **stop**, not terminate. If an upload fails, it exits 3 and does not shut
  down; the unit is not restarted.
- `make e0-aws-fetch` checks `manifest.json` (size + SHA-256, each file < 1 MB) and masks the
  account id in the image references. It then writes `results/e0_aws*` and re-makes the
  summaries. The raw copy stays in `~/.cache/skypiea/e0-aws/`.
- Timing is measured inside the cluster by the e0-probe pod, as on kind. One difference: the
  harness process now shares the node's 2 vCPUs. It is idle while the probe measures, and the
  quiet check before every run still applies.
- The controller and monitoring are **off** during E0, so nothing else competes for the 2
  vCPUs.

**E0 results (run `e0-20261104T142408Z-a24e540`, commit `a24e540`, images `a8567cc`,
m7i-flex.large, k3s v1.37.1+k3s1).** Every run succeeded on the first attempt: 320/320
sequential, 360/360 parallel-3; 0 noisy sequential, 3 of 360 noisy parallel-3. Medians in ms,
with total p95 in brackets; kind is the laptop (24 cores, local registry):

| set | env | total (p95) | image_pull | container_start | app_init | readiness_lag | first_call |
|---|---|---|---|---|---|---|---|
| sequential cached | kind | 2544 (4219) | 0 | 548 | 1221 | 498 | 121 |
| sequential cached | aws | 2734 (3779) | 0 | 512 | 1211 | 735 | 90 |
| sequential uncached | kind | 4070 (6326) | 1587 | 543 | 1181 | 533 | 123 |
| sequential uncached | aws | 5360 (8400) | 2723 | 524 | 1226 | 590 | 93 |
| parallel-3 cached | kind | 2936 (4035) | 0 | 583 | 736 | 520 | 598 |
| parallel-3 cached | aws | 5541 (6681) | 0 | 746 | 3672 | 456 | 206 |
| parallel-3 uncached | kind | 4730 (6940) | 1863 | 574 | 771 | 552 | 715 |
| parallel-3 uncached | aws | 9020 (13186) | 3464 | 729 | 3444 | 485 | 239 |

Files: `results/e0_aws{,_p3}_{runs,summary}.csv`, `results/e0_aws{,_p3}.png`,
`results/e0_aws_run_params.json`, `results/e0_aws_job.log`. The account id is masked in the
image references.

**Incident (2026-11-03):**
1. The first E0 attempt drove the node from the laptop over an SSM tunnel and `kubectl exec`.
2. Run 14 hung for about 3 h in `kubectl exec`, then failed with `i/o timeout`. The tunnel had
   died ("broken pipe"), and the clock in WSL jumped backwards (`hv_utils` TimeSync), which
   points to the laptop sleeping.
3. This was the same failure class as the earlier `docker push` stall: a long-lived connection
   from the laptop. Under the "same error twice" rule, I stopped the instance.
4. Fix: the harness moved onto the node as a systemd unit, with a timeout on every call,
   resume, and results through S3. The laptop makes only short polling calls.
5. The 13 partial runs were discarded; one clean set was measured instead. Image copy with
   `push_ecr.py` is unchanged.

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

**Functional checks on AWS (2026-11-04; checks, not results).** None of them holds a laptop
connection while it runs:
- **Replay and concurrent checks:** they run as an in-cluster Job (`replay-job.sh`, which
  starts `gateway/replay_check.py --in-cluster` or `gateway/concurrent_check.py`).
- **Metrics and Grafana:** checked through the API-server service proxy in one SSM call
  (`platform-check.sh`).
- **Helm and the `skypiea` CLI:** run through `with-tunnel.sh`, which brings the tunnel up for
  one command (capped at 120 s) and tears it down again.

| check | platform `a8567cc` | platform `b652e6b` (scale-up limit 2) |
|---|---|---|
| Scenario A+B, scale_to_zero: all ok, every call cold, no predictive warm | PASS | PASS |
| always_warm: all ok, no call cold | PASS | PASS; its 16-tool standing pool queued 14 scale-ups |
| keep_alive 30 s: all ok, the controller got predictive warms | PASS | PASS |
| markov1 stand-in: all ok, the controller got predictive warms | PASS | PASS |
| all tools back at 0 afterwards | PASS (34 s) | PASS (34 s) |
| 40 concurrent sessions (20 A + 20 B, 127 calls), scale_to_zero and markov1 | not run | PASS: 127/127 ok each; at most 2 pods starting at once (about 275 polls of the controller's state); 1 queued and 1 dequeued per policy; 0 expired |
| Prometheus targets, Skypiea metrics, Grafana 13.3.0 health | PASS | PASS |
| Grafana dashboard panels returning data | 10/12 (no predictor errors; old controller has no queue metric) | queue panel added |
| `skypiea status / policy / sessions / trace / explain` | PASS | not repeated |

Notes on these checks:
- **Image tags:** the tools stay on `a8567cc`. Platform `b652e6b` is the only new image: 0.6 MB
  of new layers, copied with `push_ecr.py`, digest in `results/aws_image_digests.csv`. It also
  carries the aws `configs/cold_start.yaml`, which the oracle and markov lead times read.
- **Why keep_alive and markov1 calls were still cold:** think gaps are scaled ×0.2, so the
  gaps (0.5–2.7 s) are shorter than a cold start (about 2.7 s cached). Predictive warms arrive
  but cannot finish in time. This is a functional limit of the scaled check, not a result.

Load tests: the functional Scenario A/B replay and a 20-50 concurrent-session test run on
this 2-vCPU node. **NFR-4 (500 concurrent sessions) is covered by the simulator, not by
this node.** We do not claim 500 sessions on 2 vCPUs.

## Real-timing scenario runs (2026-11-04/05)

Method (gateway/scenario_run.py, as an in-cluster Job; replay-job.sh CHECK=scenario):
- **Sessions:** drawn by gateway/scenario_sessions.py, with ids recorded.
  - Real traces (`results/scenario_sessions.json`): A = 20 tau2_bench test sessions (support profile),
    B = 20 TraceLab test sessions of at most 600 s (coding profile).
  - SYNTHETIC PRD scenarios (`results/scenario_sessions_prd_SYNTHETIC.json`): 20 + 20 `synthetic_prd` test
    sessions (profile prd), with the same 600 s cap.
- **Replay:** unscaled think gaps, batch by batch, 5 sessions at once.
- **Policies:** 5. The baselines are the stub stand-ins; predictive is the real predictor
  (`platform.predictor.serve=model`).
- **Idle pod-seconds:** the controller counter, from just before a policy's set-up until every tool is back at 0.
- **Image tags:** every row carries the predictor and platform image.
- **Datasets:** real and SYNTHETIC results are separate files and never mixed.

Data hygiene:
- **Markov-1 warm-up:** one TraceLab train session (`codex:ecf838ae-048e-93f2-8940-85411d567e58`,
  warm-up #48 of 300) has a call with `service_ms` = NaN although `has_timing` is true. It made B
  Markov-1 fail 3 times at set-up, because httpx refuses NaN in JSON. Since 2026-11-05 the Markov-1
  warm-up drops calls with non-finite timing and records how many (here 1 call, from warm-up #48). B
  Markov-1 was then rerun once with every other parameter the same. The three failed attempts stay in
  the CSV.
- **40-session check:** the same class of bug stopped it. 5 of its 40 drawn TraceLab test sessions
  contain 7 calls with NaN `service_ms`, and `int(NaN)` crashed the check 3 times. Not fixed overnight,
  because the orders covered only the Markov-1 warm-up.
- **Stub Markov-1:** it rounds probabilities to 6 decimals, so a ranked list can sum to 1.000002, which
  is above the validator's 1e-6 tolerance. The gateway then rejects that answer (cold path; counted as
  `predictor_errors{reason="contract"}`): 20 calls in A, 32 in B. The stub lives in the platform image,
  which was not changed during the runs.

Results: `results/scenario_aws_{calls,summary}.csv` (real), `results/scenario_aws_prd_SYNTHETIC_*`
(SYNTHETIC) and `results/concurrent40_aws.csv`. The summary is in `docs/handoff/OVERNIGHT_REPORT.md`.

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
but no keys. Since the merge into `main`, `infra/aws` in the `skypiea-prewarm` checkout has
no state of its own: run `make aws-*` targets that read state (`aws-destroy`, `aws-stop`,
`aws-start`, `push-images-aws`, `deploy-aws`) from `~/skypiea-infra`, or move the state file
into this checkout's `infra/aws/` first (it stays git-ignored). **If it is lost**, `make aws-destroy` cannot work, and teardown has to start from
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

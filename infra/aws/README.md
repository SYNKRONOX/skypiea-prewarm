# AWS cloud run (Terraform + k3s + ECR, SSM only)

Full review of the plan: [`docs/aws_plan.md`](../../docs/aws_plan.md). It covers the 50
resources, prices with sources, security group, IAM, disk sizing, E0 and teardown.

One EC2 `m7i-flex.large` in **ap-south-1** runs single-node [k3s](https://k3s.io/), with the
same Helm chart as kind (`charts/skypiea`, `values-aws.yaml`). The setup:
- **No inbound port at all.** Shell, the k3s API and the gateway demo go through SSM Session
  Manager. There is no SSH and no key pair.
- Outbound is TCP 443 only.
- IMDSv2 is required, and the root volume is a 20 GB encrypted gp3.
- The instance role is least privilege: pull from our ECR repos, read/write our bucket, the
  SSM agent.
- Everything is tagged `project=skypiea`.
- State is **local** and git-ignored; so is `tfplan`.

## Prerequisites (laptop)

- aws CLI v2 with a working profile: `aws sts get-caller-identity`.
- Your IAM user needs `operator-policy.json`, attached as a customer-managed policy.
- `session-manager-plugin`, for `aws ssm start-session`.
- docker, helm, kubectl.

## Usage

```bash
make aws-plan                    # init, validate, fmt -check, plan -out=tfplan; review it
make aws-apply                   # apply exactly that plan; writes ~/skypiea-aws-destroy-reminder.ics
make aws-tunnel                  # own terminal: 127.0.0.1:16443 -> k3s API over SSM
make aws-kubeconfig              # over SSM -> ~/.kube/skypiea-aws (outside the repo)
make build-images push-images-aws
make deploy-aws                  # E0 layout: tools at 0 + e0-probe, no controller/monitoring
make e0-aws N=30                 # E0 on AWS; then N=100 and e0-aws-parallel
make deploy-aws PLATFORM=true E0PROBE=false   # controller, gateway, monitoring
make aws-gateway                 # demo: http://127.0.0.1:8080/mcp (laptop loopback only)
make aws-shell                   # interactive SSM shell on the VM
```

Always pass the kubeconfig explicitly: `kubectl --kubeconfig ~/.kube/skypiea-aws ...`. It
points at the tunnel (`https://127.0.0.1:16443`), so it survives a stop/start (the public IP
changes) unchanged.

Scripts in `scripts/`:
- `node-run.sh`: one root command on the VM over SSM, with its exit code.
- `tunnel.sh`
- `get-k3s-config.sh`
- `empty-for-destroy.sh`
- `destroy-reminder.sh`

## Cost and teardown

About **$0.108/h, $2.60/day** while running (sources in `docs/aws_plan.md`). There is
deliberately **no auto-stop**, because it would break long E0 runs. Use the calendar reminder
written at apply (`make aws-reminder REMIND_H=<hours>` writes a new one).

```bash
make aws-stop       # pause: only the 20 GB volume is billed (about $0.06/day)
make aws-start      # resume (kubeconfig unchanged)
make aws-destroy    # back up results; type "destroy skypiea"; empties bucket + ECR, then destroys all
```

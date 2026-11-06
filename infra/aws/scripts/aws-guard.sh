#!/usr/bin/env bash
# Safety gate in front of every `make aws-*` target (mk/infra.mk). Runs BEFORE any AWS or
# Terraform call and changes nothing.
#   aws-guard.sh <target>             needs aws (and terraform) + configured credentials
#   aws-guard.sh --create <target>    also needs AWS_CONFIRM=yes (creates or starts billable resources)
# Credentials count as configured if AWS_ACCESS_KEY_ID, AWS_PROFILE or AWS_WEB_IDENTITY_TOKEN_FILE
# is set, or if ~/.aws/credentials or ~/.aws/config exists. The files are not read.
set -euo pipefail
create=0
if [ "${1:-}" = --create ]; then create=1; shift; fi
target="${1:-aws target}"
refuse() { echo "make $target refused: $*" >&2; echo "Nothing was created or changed. AWS is optional: README, Tier 3." >&2; exit 1; }

if [ $create = 1 ] && [ "${AWS_CONFIRM:-}" != yes ]; then
  refuse "this creates or starts AWS resources that cost money in YOUR account (about \$0.108/h while the VM runs, docs/aws_plan.md). Read README Tier 3, then run: make $target AWS_CONFIRM=yes"
fi
command -v aws >/dev/null 2>&1 || refuse "the aws CLI v2 is not installed (https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)"
command -v terraform >/dev/null 2>&1 || refuse "terraform is not installed (https://developer.hashicorp.com/terraform/install)"
cfg="${AWS_SHARED_CREDENTIALS_FILE:-$HOME/.aws/credentials}"
conf="${AWS_CONFIG_FILE:-$HOME/.aws/config}"
if [ -z "${AWS_ACCESS_KEY_ID:-}${AWS_PROFILE:-}${AWS_WEB_IDENTITY_TOKEN_FILE:-}" ] && [ ! -e "$cfg" ] && [ ! -e "$conf" ]; then
  refuse "no AWS credentials are configured (aws configure, or AWS_PROFILE=<profile>)"
fi
case "$target" in
  aws-plan|aws-apply) ;;
  *) [ -f infra/aws/terraform.tfstate ] || refuse "no Terraform state in infra/aws/ (run make aws-plan and make aws-apply AWS_CONFIRM=yes from THIS folder first)" ;;
esac
exit 0

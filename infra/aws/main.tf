locals {
  # Single source of truth for the tool list: tools/tool_map.yaml (+ the E0 probe and platform).
  tools       = keys(yamldecode(file("${path.module}/../../tools/tool_map.yaml")).servers)
  images      = concat(local.tools, ["e0-probe", "platform", "predictor"])
  account_id  = data.aws_caller_identity.me.account_id
  ecr_host    = "${local.account_id}.dkr.ecr.${var.region}.amazonaws.com"
  bucket_name = "skypiea-${random_id.bucket.hex}"
  repo_arns   = [for r in aws_ecr_repository.img : r.arn]
}

data "aws_caller_identity" "me" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical; these AMIs ship the SSM agent (snap)
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

# ---------------- network: NO inbound rules at all ----------------
# Operator access (shell, k3s API, gateway demo) goes through SSM Session Manager, which the
# agent on the VM opens as an OUTBOUND HTTPS connection. Nothing listens to the internet.
resource "aws_security_group" "node" {
  name        = "skypiea-node"
  description = "Skypiea k3s node: no inbound; HTTPS out only (SSM, ECR, S3, k3s install)"
  vpc_id      = data.aws_vpc.default.id
  tags        = { Name = "skypiea-node" }
}

# The only rule. Outbound TCP 443: SSM/ssmmessages, ECR API + layer downloads (S3), the AWS
# APIs, get.k3s.io + GitHub releases (k3s), snapcraft (aws-cli). DNS (VPC resolver) and time
# sync (169.254.169.123) are link-local and not filtered by security groups. No port 80:
# nothing on the VM uses apt.
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.node.id
  description       = "HTTPS out: SSM, ECR, S3, AWS APIs, k3s install, snap"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  tags              = { Name = "skypiea-node-https-out" }
}

# ---------------- IAM: instance role, inline least-privilege policies ----------------
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "skypiea-node"
  description        = "Skypiea k3s node: pull our ECR repos, read/write our bucket, SSM sessions"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# ECR: pull only, and only from the skypiea/* repositories created here.
data "aws_iam_policy_document" "ecr_pull" {
  statement {
    sid       = "EcrToken"
    actions   = ["ecr:GetAuthorizationToken"] # account-level API: cannot be scoped to a repo
    resources = ["*"]
  }
  statement {
    sid       = "EcrPullOurRepos"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = local.repo_arns
  }
}

resource "aws_iam_role_policy" "ecr_pull" {
  name   = "skypiea-ecr-pull"
  role   = aws_iam_role.node.id
  policy = data.aws_iam_policy_document.ecr_pull.json
}

# S3: read/write objects in the one bucket created here.
data "aws_iam_policy_document" "bucket_rw" {
  statement {
    sid       = "ListOurBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.data.arn]
  }
  statement {
    sid       = "ObjectsInOurBucket"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.data.arn}/*"]
  }
}

resource "aws_iam_role_policy" "bucket_rw" {
  name   = "skypiea-bucket-rw"
  role   = aws_iam_role.node.id
  policy = data.aws_iam_policy_document.bucket_rw.json
}

# SSM Session Manager only: the minimal instance policy from the Session Manager docs
# ("Create a custom IAM role for Session Manager"), instead of AmazonSSMManagedInstanceCore
# (which also grants Run Command, Parameter Store reads, inventory, ...). No session logging
# to S3/CloudWatch and no KMS, so those statements are left out.
data "aws_iam_policy_document" "ssm_session" {
  statement {
    sid = "SessionManagerAgent"
    actions = [
      "ssm:UpdateInstanceInformation",
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"] # these agent APIs do not support resource-level scoping
  }
}

resource "aws_iam_role_policy" "ssm_session" {
  name   = "skypiea-ssm-session"
  role   = aws_iam_role.node.id
  policy = data.aws_iam_policy_document.ssm_session.json
}

resource "aws_iam_instance_profile" "node" {
  name = "skypiea-node"
  role = aws_iam_role.node.name
}

# ---------------- ECR: one repo per image, keep last 3 images ----------------
resource "aws_ecr_repository" "img" {
  for_each             = toset(local.images)
  name                 = "skypiea/${each.key}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # `make aws-destroy` also empties them first; images rebuild from git
  encryption_configuration {
    encryption_type = "AES256"
  }
  image_scanning_configuration {
    scan_on_push = false
  }
}

resource "aws_ecr_lifecycle_policy" "keep3" {
  for_each   = aws_ecr_repository.img
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the last 3 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 3 }
      action       = { type = "expire" }
    }]
  })
}

# ---------------- S3: models and results (private, encrypted, TLS only) ----------------
resource "random_id" "bucket" {
  byte_length = 4
}

resource "aws_s3_bucket" "data" {
  bucket        = local.bucket_name
  force_destroy = false # `make aws-destroy` empties it explicitly, after you have a backup
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    object_ownership = "BucketOwnerEnforced" # ACLs disabled
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "bucket_tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "data" {
  bucket     = aws_s3_bucket.data.id
  policy     = data.aws_iam_policy_document.bucket_tls_only.json
  depends_on = [aws_s3_bucket_public_access_block.data]
}

# ---------------- the k3s VM ----------------
resource "aws_instance" "node" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  vpc_security_group_ids      = [aws_security_group.node.id]
  iam_instance_profile        = aws_iam_instance_profile.node.name
  associate_public_ip_address = true # outbound only (no inbound rules); avoids a paid NAT gateway

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    delete_on_termination = true
    tags                  = { project = "skypiea", Name = "skypiea-node-root" }
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 1          # pods cannot reach the node's role credentials
    instance_metadata_tags      = "disabled"
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    k3s_version = var.k3s_version
    region      = var.region
    ecr_host    = local.ecr_host
  })
  user_data_replace_on_change = true

  # A stopped instance reads associate_public_ip_address = false; without this, a plan made
  # while it is stopped wants to REPLACE it (seen 2026-11-04).
  lifecycle {
    ignore_changes = [associate_public_ip_address]
  }

  tags = { Name = "skypiea-node" }
}

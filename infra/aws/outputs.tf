output "instance_id" {
  description = "SSM target for `make aws-tunnel`, `aws-shell` and E0 node commands"
  value       = aws_instance.node.id
}

output "ecr_registry" {
  value = local.ecr_host
}

output "ecr_repository_urls" {
  value = { for k, r in aws_ecr_repository.img : k => r.repository_url }
}

output "bucket_name" {
  value = aws_s3_bucket.data.bucket
}

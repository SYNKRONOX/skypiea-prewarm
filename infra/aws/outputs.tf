output "instance_id" {
  value = aws_instance.node.id
}

output "instance_ip" {
  description = "Public IP (changes after stop/start; `make aws-start` refreshes the kubeconfig)"
  value       = aws_instance.node.public_ip
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

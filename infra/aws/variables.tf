variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "instance_type" {
  type    = string
  default = "m7i-flex.large" # 2 vCPU / 8 GiB; free-tier eligible on this account (checked)
}

variable "k3s_version" {
  type    = string
  default = "v1.37.1+k3s1"
}

variable "root_volume_gb" {
  description = "Root gp3 size. 20 GB: measured peak use about 9 GB (see docs/aws_plan.md), which keeps the disk under the kubelet image-GC threshold (85%) so cached E0 runs never lose images to GC."
  type        = number
  default     = 20
  validation {
    condition     = var.root_volume_gb >= 12
    error_message = "Below 12 GB the measured peak (about 9 GB) crosses the kubelet image-GC threshold (85%)."
  }
}

variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "instance_type" {
  type    = string
  default = "m7i-flex.large" # 2 vCPU / 8 GiB; checked free-tier eligible before apply
}

variable "my_ip_cidr" {
  description = "Your public IP as a /32. The ONLY source allowed for SSH (22) and k3s API (6443)."
  type        = string
  validation {
    condition     = can(regex("^\\d+\\.\\d+\\.\\d+\\.\\d+/32$", var.my_ip_cidr))
    error_message = "my_ip_cidr must be a single IPv4 address as /32 (never 0.0.0.0/0)."
  }
}

variable "ssh_public_key_path" {
  type    = string
  default = "~/.ssh/skypiea-aws.pub"
}

variable "k3s_version" {
  type    = string
  default = "v1.37.1+k3s1"
}

variable "root_volume_gb" {
  type    = number
  default = 30
}

variable "bucket_force_destroy" {
  description = "Allow `terraform destroy` to delete the bucket even if it holds objects."
  type        = bool
  default     = false
}

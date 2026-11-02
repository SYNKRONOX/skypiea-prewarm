terraform {
  required_version = ">= 1.9"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.71" }
    random = { source = "hashicorp/random", version = "~> 3.9" }
  }
  # State stays LOCAL (terraform.tfstate, git-ignored). Never commit it.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "skypiea" }
  }
}

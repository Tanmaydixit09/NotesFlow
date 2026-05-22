# ─────────────────────────────────────────────────────────────────
# NoteFlow — Terraform Variables
# ─────────────────────────────────────────────────────────────────

variable "aws_region" {
  description = "AWS region to deploy NoteFlow server"
  type        = string
  default     = "ap-south-1"   # Mumbai — closest to India
}

variable "ami_id" {
  description = "Ubuntu 22.04 LTS AMI ID"
  type        = string
  default     = "ami-0f5ee92e2d63afc18"  # Ubuntu 22.04 LTS - Mumbai
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t2.medium"   # 2 vCPU, 4GB RAM — enough for Minikube
}

variable "key_pair_name" {
  description = "AWS key pair name for SSH access"
  type        = string
  default     = "noteflow-key"
}

variable "dockerhub_user" {
  description = "Docker Hub username"
  type        = string
  default     = "tanmaydixit09"
}

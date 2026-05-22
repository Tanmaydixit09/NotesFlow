# ─────────────────────────────────────────────────────────────────
# NoteFlow — Terraform Infrastructure
# Provisions AWS EC2 instance for Kubernetes cluster
# INT377 — Cloud Computing and DevOps Essentials
# ─────────────────────────────────────────────────────────────────

terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ── Provider ──────────────────────────────────────────────────────
provider "aws" {
  region = var.aws_region
}

# ── Security Group ────────────────────────────────────────────────
resource "aws_security_group" "noteflow_sg" {
  name        = "noteflow-security-group"
  description = "Security group for NoteFlow application"

  # SSH access
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "SSH access"
  }

  # Flask app
  ingress {
    from_port   = 5000
    to_port     = 5000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "NoteFlow Flask app"
  }

  # Kubernetes NodePort
  ingress {
    from_port   = 30500
    to_port     = 30500
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Kubernetes NodePort - NoteFlow"
  }

  # Prometheus
  ingress {
    from_port   = 30900
    to_port     = 30900
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Kubernetes NodePort - Prometheus"
  }

  # Grafana
  ingress {
    from_port   = 30300
    to_port     = 30300
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Kubernetes NodePort - Grafana"
  }

  # HTTP
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTP"
  }

  # All outbound traffic
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound traffic"
  }

  tags = {
    Name    = "noteflow-sg"
    Project = "NoteFlow"
    Course  = "INT377"
  }
}

# ── EC2 Instance ──────────────────────────────────────────────────
resource "aws_instance" "noteflow_server" {
  ami                    = var.ami_id
  instance_type          = var.instance_type
  key_name               = var.key_pair_name
  vpc_security_group_ids = [aws_security_group.noteflow_sg.id]

  # Storage
  root_block_device {
    volume_size = 20
    volume_type = "gp2"
  }

  # Startup script — installs Docker, kubectl, and pulls NoteFlow image
  user_data = <<-EOF
    #!/bin/bash
    set -e

    echo "=== NoteFlow Server Setup ==="

    # Update system
    apt-get update -y
    apt-get upgrade -y

    # Install Docker
    apt-get install -y docker.io curl wget git
    systemctl start docker
    systemctl enable docker
    usermod -aG docker ubuntu

    # Install kubectl
    curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
    chmod +x kubectl
    mv kubectl /usr/local/bin/

    # Install Minikube
    curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
    install minikube-linux-amd64 /usr/local/bin/minikube

    # Pull NoteFlow Docker image
    docker pull ${var.dockerhub_user}/noteflow-app:latest

    echo "=== Setup Complete ==="
    echo "NoteFlow server is ready!"
  EOF

  tags = {
    Name    = "noteflow-server"
    Project = "NoteFlow"
    Course  = "INT377"
  }
}

# ── Elastic IP (static IP) ────────────────────────────────────────
resource "aws_eip" "noteflow_eip" {
  instance = aws_instance.noteflow_server.id
  domain   = "vpc"

  tags = {
    Name    = "noteflow-eip"
    Project = "NoteFlow"
  }
}

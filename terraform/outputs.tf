# ─────────────────────────────────────────────────────────────────
# NoteFlow — Terraform Outputs
# ─────────────────────────────────────────────────────────────────

output "server_public_ip" {
  description = "Public IP address of NoteFlow server"
  value       = aws_eip.noteflow_eip.public_ip
}

output "server_instance_id" {
  description = "EC2 Instance ID"
  value       = aws_instance.noteflow_server.id
}

output "ssh_command" {
  description = "SSH command to connect to server"
  value       = "ssh -i ${var.key_pair_name}.pem ubuntu@${aws_eip.noteflow_eip.public_ip}"
}

output "noteflow_url" {
  description = "NoteFlow application URL"
  value       = "http://${aws_eip.noteflow_eip.public_ip}:30500"
}

output "prometheus_url" {
  description = "Prometheus URL"
  value       = "http://${aws_eip.noteflow_eip.public_ip}:30900"
}

output "grafana_url" {
  description = "Grafana URL"
  value       = "http://${aws_eip.noteflow_eip.public_ip}:30300"
}

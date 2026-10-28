# Latest Amazon Linux 2023 AMI, looked up from AWS's public SSM parameter.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  fqdn = "${var.subdomain}.${var.hosted_zone_name}"
}

resource "aws_instance" "app" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.web.id]
  iam_instance_profile   = aws_iam_instance_profile.app.name

  # The app's source is shipped inside user_data, so changing the code
  # replaces the instance with a fresh one running the new version.
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region       = var.region
    fqdn         = local.fqdn
    acme_email   = var.acme_email
    bucket       = aws_s3_bucket.files.bucket
    main_py      = base64gzip(file("${path.module}/../app/main.py"))
    storage_py   = base64gzip(file("${path.module}/../app/storage.py"))
    requirements = base64gzip(file("${path.module}/../requirements.txt"))
  })
  user_data_replace_on_change = true

  tags = { Name = "${var.project}-app" }

  lifecycle {
    # A newer AMI shouldn't silently replace the running server.
    ignore_changes = [ami]
  }
}

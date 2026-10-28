# The hosted zone was created by hand in Route 53, and the Porkbun
# nameservers point at it. Terraform only adds the record for the app.
data "aws_route53_zone" "main" {
  name         = var.hosted_zone_name
  private_zone = false
}

resource "aws_route53_record" "app" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = local.fqdn
  type    = "A"
  ttl     = 300
  records = [aws_instance.app.public_ip]
}

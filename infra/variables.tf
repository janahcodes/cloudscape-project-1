variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-2"
}

variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "clouddrop"
}

variable "hosted_zone_name" {
  description = "An existing Route 53 public hosted zone you control, e.g. \"cloudscape.lol\"."
  type        = string
}

variable "subdomain" {
  description = "Record created inside the zone. The site is served at <subdomain>.<hosted_zone_name>."
  type        = string
  default     = "app"
}

variable "acme_email" {
  description = "Email Let's Encrypt uses for certificate expiry notices."
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type. t3.micro is free-tier eligible."
  type        = string
  default     = "t3.micro"
}

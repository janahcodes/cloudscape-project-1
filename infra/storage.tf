# Object storage for uploaded files. New S3 buckets block all public access
# and encrypt objects at rest (SSE-S3) by default.

resource "random_id" "suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "files" {
  bucket        = "${var.project}-files-${random_id.suffix.hex}"
  force_destroy = true # lets `terraform destroy` clean up a coursework bucket
}

# Room files are temporary: S3 deletes them one day after upload, so the
# bucket never grows and nothing has to run a cleanup job.
resource "aws_s3_bucket_lifecycle_configuration" "files" {
  bucket = aws_s3_bucket.files.id

  rule {
    id     = "expire-room-files"
    status = "Enabled"
    filter {
      prefix = "rooms/"
    }
    expiration {
      days = 1
    }
  }
}

# The instance role is the only identity the running service has. It can
# list, read, write and delete objects under "rooms/" in exactly one bucket,
# and nothing else.

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${var.project}-instance-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

data "aws_iam_policy_document" "app" {
  statement {
    sid       = "ListRoomFiles"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.files.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["rooms/*"]
    }
  }

  statement {
    sid       = "ReadWriteRoomFiles"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.files.arn}/rooms/*"]
  }
}

resource "aws_iam_role_policy" "app" {
  name   = "${var.project}-s3-only"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app.json
}

resource "aws_iam_instance_profile" "app" {
  name = "${var.project}-instance-profile"
  role = aws_iam_role.app.name
}

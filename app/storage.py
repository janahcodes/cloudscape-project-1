"""Thin wrapper around the S3 bucket that holds uploaded files.

Every file belongs to a room and is stored under "rooms/<CODE>/<id>", where
<id> is "<uuid>/<safe-filename>". An S3 lifecycle rule deletes everything
under "rooms/" one day after upload.

The service never handles AWS keys. boto3 picks up temporary credentials
from the EC2 instance role via the instance metadata service (IMDSv2).
"""

import re
import secrets
import uuid
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError

# Room codes skip I, L, O, 0 and 1 so they are easy to read and type.
ROOM_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
ROOM_PATTERN = re.compile(r"^[A-HJKMNP-Z2-9]{6}$")
# File ids look like "<uuid>/<safe-filename>". Anything else is rejected so a
# caller cannot reach objects outside their room.
FILE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}/[A-Za-z0-9._-]{1,128}$")
UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "heic", "avif"}


@dataclass
class StoredFile:
    id: str
    name: str
    size: int
    last_modified: str
    is_image: bool


def new_room_code() -> str:
    return "".join(secrets.choice(ROOM_ALPHABET) for _ in range(6))


def is_valid_room(code: str) -> bool:
    return bool(ROOM_PATTERN.match(code))


def is_valid_file_id(file_id: str) -> bool:
    return bool(FILE_ID_PATTERN.match(file_id))


def safe_filename(name: str) -> str:
    """Strip directories and unsafe characters from a client-supplied name."""
    base = name.replace("\\", "/").split("/")[-1].strip()
    cleaned = UNSAFE_CHARS.sub("_", base).strip("._")
    return (cleaned or "file")[:128]


def is_image(name: str) -> bool:
    return name.rsplit(".", 1)[-1].lower() in IMAGE_EXTENSIONS


class FileStore:
    def __init__(self, bucket: str, region: str, client=None):
        self.bucket = bucket
        # Regional endpoint: presigned URLs built for the global endpoint get
        # redirected by S3 outside us-east-1, and the redirect breaks the signature.
        self.s3 = client or boto3.client(
            "s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com"
        )

    @staticmethod
    def _prefix(room: str) -> str:
        return f"rooms/{room}/"

    def ping(self) -> bool:
        # A one-key listing rather than HeadBucket: the IAM role may only
        # list keys under "rooms/".
        try:
            self.s3.list_objects_v2(Bucket=self.bucket, Prefix="rooms/", MaxKeys=1)
            return True
        except ClientError:
            return False

    def put(self, room: str, filename: str, data: bytes, content_type: str) -> StoredFile:
        name = safe_filename(filename)
        file_id = f"{uuid.uuid4().hex}/{name}"
        self.s3.put_object(
            Bucket=self.bucket,
            Key=self._prefix(room) + file_id,
            Body=data,
            ContentType=content_type or "application/octet-stream",
        )
        return StoredFile(
            id=file_id, name=name, size=len(data), last_modified="", is_image=is_image(name)
        )

    def list(self, room: str, limit: int = 100) -> list[StoredFile]:
        prefix = self._prefix(room)
        resp = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix, MaxKeys=limit)
        files = []
        for obj in resp.get("Contents", []):
            file_id = obj["Key"][len(prefix):]
            name = file_id.split("/", 1)[-1]
            files.append(
                StoredFile(
                    id=file_id,
                    name=name,
                    size=obj["Size"],
                    last_modified=obj["LastModified"].isoformat(),
                    is_image=is_image(name),
                )
            )
        return sorted(files, key=lambda f: f.last_modified, reverse=True)

    def exists(self, room: str, file_id: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=self._prefix(room) + file_id)
            return True
        except ClientError as err:
            if err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def download_url(
        self, room: str, file_id: str, expires: int = 300, attachment: bool = True
    ) -> str:
        # The bucket stays private; clients get a short-lived signed URL.
        # attachment=True makes the browser save the file instead of opening it.
        params = {"Bucket": self.bucket, "Key": self._prefix(room) + file_id}
        if attachment:
            name = file_id.split("/", 1)[-1]
            params["ResponseContentDisposition"] = f'attachment; filename="{name}"'
        return self.s3.generate_presigned_url("get_object", Params=params, ExpiresIn=expires)

    def delete(self, room: str, file_id: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=self._prefix(room) + file_id)

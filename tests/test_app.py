"""Tests for the web service. S3 is replaced by moto's in-memory fake, so
they run offline and touch no real AWS account."""

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from app.main import app, get_store
from app.storage import (
    FileStore,
    is_valid_file_id,
    is_valid_room,
    new_room_code,
    safe_filename,
)

REGION = "us-east-2"
BUCKET = "test-bucket"


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        boto3.client("s3", region_name=REGION).create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION}
        )
        yield FileStore(bucket=BUCKET, region=REGION)


@pytest.fixture
def client(store):
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def room(client):
    return client.post("/api/rooms").json()["code"]


def upload(client, room, name="hello.txt", data=b"hello"):
    return client.post(f"/api/rooms/{room}/files", files={"file": (name, data, "text/plain")})


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_ready_when_bucket_reachable(client):
    assert client.get("/ready").status_code == 200


def test_home_page_renders(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Start a room" in res.text


def test_create_room_gives_valid_code(client):
    res = client.post("/api/rooms")
    assert res.status_code == 201
    assert is_valid_room(res.json()["code"])


def test_room_page_and_qr_code(client, room):
    res = client.get(f"/r/{room}")
    assert res.status_code == 200
    assert room in res.text
    qr = client.get(f"/r/{room}/qr.svg")
    assert qr.headers["content-type"] == "image/svg+xml"
    assert "<svg" in qr.text


def test_lowercase_room_code_redirects(client, room):
    res = client.get(f"/r/{room.lower()}", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == f"/r/{room}"


def test_invalid_room_is_404(client):
    assert client.get("/r/NOPE").status_code == 404
    assert client.get("/api/rooms/abc!!!/files").status_code == 404


def test_upload_then_list(client, room):
    assert upload(client, room).status_code == 201
    files = client.get(f"/api/rooms/{room}/files").json()
    assert [f["name"] for f in files] == ["hello.txt"]
    assert files[0]["size"] == 5
    assert "preview" not in files[0]


def test_rooms_are_separate(client, room):
    other = client.post("/api/rooms").json()["code"]
    upload(client, room)
    assert client.get(f"/api/rooms/{other}/files").json() == []


def test_image_gets_preview_url(client, room):
    upload(client, room, name="photo.jpg", data=b"\xff\xd8fakejpeg")
    f = client.get(f"/api/rooms/{room}/files").json()[0]
    assert f["is_image"] is True
    assert f["preview"].startswith(f"https://s3.{REGION}.amazonaws.com/")
    assert "attachment" not in f["preview"]


def test_download_redirects_to_signed_regional_url(client, room):
    f = upload(client, room).json()
    res = client.get(f"/api/rooms/{room}/files/{f['id']}", follow_redirects=False)
    assert res.status_code == 302
    url = res.headers["location"]
    # Regression test: a URL for the global endpoint broke outside us-east-1.
    assert url.startswith(f"https://s3.{REGION}.amazonaws.com/{BUCKET}/rooms/{room}/")
    assert "X-Amz-Signature=" in url and "X-Amz-Expires=300" in url
    assert "attachment" in url


def test_delete(client, room):
    f = upload(client, room).json()
    assert client.delete(f"/api/rooms/{room}/files/{f['id']}").status_code == 204
    assert client.get(f"/api/rooms/{room}/files").json() == []


def test_missing_file_is_404(client, room):
    file_id = "0" * 32 + "/nope.txt"
    assert client.get(f"/api/rooms/{room}/files/{file_id}", follow_redirects=False).status_code == 404
    assert client.delete(f"/api/rooms/{room}/files/{file_id}").status_code == 404


def test_invalid_file_id_is_rejected(client, room):
    assert client.delete(f"/api/rooms/{room}/files/not-a-valid-id").status_code == 400
    assert client.get(f"/api/rooms/{room}/files/../../etc/passwd").status_code in (400, 404)


def test_empty_upload_is_rejected(client, room):
    assert upload(client, room, data=b"").status_code == 400


def test_oversize_upload_is_rejected(client, room, monkeypatch):
    monkeypatch.setattr("app.main.MAX_UPLOAD_BYTES", 10)
    assert upload(client, room, data=b"x" * 11).status_code == 413


def test_files_are_stored_under_rooms_prefix(client, room, store):
    upload(client, room)
    keys = [o["Key"] for o in store.s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
    # The IAM role and the S3 lifecycle rule both rely on this prefix.
    assert all(k.startswith(f"rooms/{room}/") for k in keys)


def test_room_codes_are_valid_and_random():
    codes = {new_room_code() for _ in range(200)}
    assert all(is_valid_room(c) for c in codes)
    assert len(codes) > 190
    assert not is_valid_room("ABC1O0")  # 1, O and 0 are never used


def test_safe_filename_strips_paths_and_unsafe_characters():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("my report (final).pdf") == "my_report__final_.pdf"
    assert safe_filename("...") == "file"


def test_file_id_pattern():
    assert is_valid_file_id("a" * 32 + "/file.txt")
    assert not is_valid_file_id("../secret")
    assert not is_valid_file_id("a" * 32 + "/../x")

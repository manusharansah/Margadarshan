import pytest

from gis_backend.fetch_artifact import fetch


class FakeS3:
    def __init__(self):
        self.calls = []

    def download_file(self, bucket, key, dest):
        self.calls.append((bucket, key))
        open(dest, "wb").write(b"payload")


def test_fetch_downloads_atomically(tmp_path):
    s3, dest = FakeS3(), tmp_path / "d" / "network.json.gz"
    assert fetch("s3://my-bucket/path/network.json.gz", dest, s3)
    assert s3.calls == [("my-bucket", "path/network.json.gz")]
    assert dest.read_bytes() == b"payload" and not dest.with_suffix(".part").exists()


@pytest.mark.parametrize("bad", ["http://x/y", "s3://bucket", "s3:///key", "network.json.gz"])
def test_fetch_rejects_bad_uris(tmp_path, bad):
    with pytest.raises(ValueError):
        fetch(bad, tmp_path / "x", FakeS3())

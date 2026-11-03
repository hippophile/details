"""Failing DB tests must not print the test DSN password (pytest shows fixture values via repr)."""
from urllib.parse import urlsplit


def test_db_url_repr_hides_password(db_url):
    pw = urlsplit(str(db_url)).password
    assert pw and pw not in repr(db_url)
    assert "***" in repr(db_url)
    assert str(db_url).startswith("postgresql://")  # still usable as a DSN

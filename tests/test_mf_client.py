"""MFClient tests: parsing/classification offline; network paths marked `live`."""

import pandas as pd
import pytest

from data.mf_client import MFClient, classify_scheme


def test_classify_scheme():
    assert classify_scheme("HDFC Large Cap Fund") == "large_cap"
    assert classify_scheme("Kotak Emerging Equity (Mid Cap)") == "mid_cap"
    assert classify_scheme("ICICI Pru Corporate Bond Fund") == "debt"
    assert classify_scheme("SBI Gold Fund") == "gold_or_other"
    assert classify_scheme("Nippon India Liquid Fund") == "debt"


def test_nav_history_parsing(tmp_path, monkeypatch):
    client = MFClient(cache_root=tmp_path)
    payload = {
        "meta": {"scheme_code": 120503, "scheme_name": "Test Fund"},
        "data": [
            {"date": f"0{i}-08-2026", "nav": str(100 + i * 1.5)} for i in range(1, 10)
        ] + [
            {"date": f"2{i}-07-2026", "nav": str(90 + i)} for i in range(1, 9)
        ],
    }
    import json
    cache_dir = tmp_path / "var" / "cache" / "mf"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "navhist_120503.json"
    cache_file.write_text(json.dumps(payload), encoding="utf-8")
    # touch mtime to now so cache is considered fresh
    import os
    os.utime(cache_file)

    hist = client.get_nav_history(120503)
    assert isinstance(hist.index, pd.DatetimeIndex)
    assert list(hist.columns) == ["nav"]
    assert len(hist) == 17
    assert hist["nav"].iloc[-1] == pytest.approx(113.5)


@pytest.mark.live
def test_real_amfi_and_history():
    """Requires network. Pulls real AMFI NAVs + one scheme history."""
    client = MFClient()
    navs = client.latest_navs()
    assert len(navs) > 5000
    hist = client.get_nav_history(120503)  # Parag Parikh Flexi Cap
    assert len(hist) > 200

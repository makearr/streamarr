from streamarr import config


def test_defaults_and_generated_secrets():
    cfg = config.get()
    assert cfg["streamarr"]["port"] == 8585
    assert len(cfg["streamarr"]["api_key"]) == 32
    assert cfg["downloads"]["path"].endswith("downloads")


def test_normalize_url():
    assert config.normalize_url("10.0.1.5:8989") == "http://10.0.1.5:8989"
    assert config.normalize_url("https://arr.example.com/") == "https://arr.example.com"
    assert config.normalize_url("HTTP://x") == "HTTP://x"
    assert config.normalize_url("") == ""


def test_update_roundtrip():
    config.update("downloads", {"speed_limit_kbps": 1234})
    assert config.load()["downloads"]["speed_limit_kbps"] == 1234
    config.update("downloads", {"speed_limit_kbps": 0})

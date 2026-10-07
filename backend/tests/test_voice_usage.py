from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import shutil
import subprocess
import time
import tempfile

import pytest
import redis

from backend.app import config, voice_usage


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expirations = {}

    def eval(self, script, keys, key, limit, expiry):
        used = int(self.values.get(key, 0))
        if used >= limit:
            return [0, used]
        self.values[key] = used + 1
        self.expirations[key] = expiry
        return [1, used + 1]

    def mget(self, keys):
        return [self.values.get(key) for key in keys]


@pytest.fixture
def counters(monkeypatch):
    store = FakeRedis()
    monkeypatch.setattr(voice_usage, "_redis", store)
    monkeypatch.setattr(voice_usage, "VOICE_STT_DAILY_LIMIT", 2)
    monkeypatch.setattr(voice_usage, "VOICE_TTS_DAILY_LIMIT", 2)
    monkeypatch.setattr(voice_usage, "_utc_now", lambda: datetime(2026, 10, 6, 23, 59, 59, tzinfo=timezone.utc))
    return store


@pytest.mark.parametrize("feature", ["stt", "tts"])
def test_quota_final_request_exhaustion_and_user_isolation(counters, feature):
    assert voice_usage.reserve_voice("user-a", feature).remaining == 1
    assert voice_usage.reserve_voice("user-a", feature).remaining == 0
    with pytest.raises(voice_usage.QuotaExhausted) as caught:
        voice_usage.reserve_voice("user-a", feature)
    assert caught.value.usage.used == 2
    assert voice_usage.reserve_voice("user-b", feature).remaining == 1
    assert voice_usage.get_voice_usage("user-a")[feature]["remaining"] == 0
    assert voice_usage.get_voice_usage("user-a")["tts" if feature == "stt" else "stt"]["used"] == 0


def test_utc_midnight_uses_new_key_and_expiry(counters, monkeypatch):
    usage = voice_usage.reserve_voice("user", "stt")
    assert usage.resets_at == "2026-10-07T00:00:00+00:00"
    assert counters.expirations["voice:stt:user:2026-10-06"] == 1791331200
    monkeypatch.setattr(voice_usage, "_utc_now", lambda: datetime(2026, 10, 7, tzinfo=timezone.utc))
    assert voice_usage.get_voice_usage("user")["stt"]["used"] == 0
    assert voice_usage.reserve_voice("user", "stt").remaining == 1


def test_unavailable_or_corrupt_store_fails_closed(counters, monkeypatch):
    counters.values["voice:stt:user:2026-10-06"] = "corrupt"
    with pytest.raises(voice_usage.UsageUnavailable):
        voice_usage.get_voice_usage("user")
    with pytest.raises(voice_usage.UsageUnavailable):
        voice_usage.reserve_voice("user", "stt")


@pytest.mark.parametrize("setting,expected", [("0", 0), ("10", 10)])
def test_configurable_limits(monkeypatch, setting, expected):
    monkeypatch.setenv("VOICE_STT_DAILY_LIMIT", setting)
    assert config._voice_limit("VOICE_STT_DAILY_LIMIT", 10) == expected


@pytest.mark.parametrize("setting", ["-1", "bad", "1.5"])
def test_invalid_limits_rejected(monkeypatch, setting):
    monkeypatch.setenv("VOICE_STT_DAILY_LIMIT", setting)
    with pytest.raises(RuntimeError):
        config._voice_limit("VOICE_STT_DAILY_LIMIT", 10)


def test_real_lua_is_atomic_under_concurrent_requests(tmp_path, monkeypatch):
    executable = shutil.which("redis-server")
    if not executable:
        pytest.skip("redis-server is required for the Lua concurrency check")
    socket_directory = tempfile.TemporaryDirectory(prefix="voice-redis-", dir="/private/tmp")
    socket = socket_directory.name + "/redis.sock"
    process = subprocess.Popen([
        executable, "--port", "0", "--unixsocket", socket,
        "--save", "", "--appendonly", "no", "--dir", str(tmp_path),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    connection = redis.Redis(unix_socket_path=socket, decode_responses=True)
    try:
        for _ in range(100):
            try:
                if connection.ping():
                    break
            except redis.RedisError:
                if process.poll() is not None:
                    pytest.fail("Isolated Redis exited before accepting connections")
                time.sleep(0.02)
        else:
            pytest.fail("Isolated Redis did not start")
        monkeypatch.setattr(voice_usage, "_redis", connection)
        monkeypatch.setattr(voice_usage, "VOICE_STT_DAILY_LIMIT", 10)
        def attempt(_):
            try:
                return voice_usage.reserve_voice("concurrent", "stt")
            except voice_usage.QuotaExhausted:
                return None
        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(attempt, range(40)))
        assert sum(result is not None for result in results) == 10
        assert voice_usage.get_voice_usage("concurrent")["stt"]["used"] == 10
        keys = connection.keys("voice:stt:concurrent:*")
        assert len(keys) == 1
        assert 0 < connection.ttl(keys[0]) <= 86400
    finally:
        connection.close()
        process.terminate()
        process.wait(timeout=5)
        socket_directory.cleanup()

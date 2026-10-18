from tools.platform_proof import dfltcc_ready


def test_dfltcc_requires_s390x_cpu_flag_and_loaded_zlib_support():
    evidence = {
        "host": {
            "architecture": "s390x",
            "dflt_flag": True,
            "zlib_dfltcc_support": True,
        }
    }
    assert dfltcc_ready(evidence)

    for key, value in (
        ("architecture", "x86_64"),
        ("dflt_flag", False),
        ("zlib_dfltcc_support", False),
        ("zlib_dfltcc_support", None),
    ):
        unsupported = {"host": {**evidence["host"], key: value}}
        assert not dfltcc_ready(unsupported)


def test_dfltcc_guard_rejects_missing_host_evidence():
    assert not dfltcc_ready({})
    assert not dfltcc_ready({"host": None})

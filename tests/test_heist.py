from tools.heist import build_heist_events, run_heist


def test_heist_has_hop_two_transfer_and_later_cashout():
    accounts = {
        "A": {"source": "A:DEMO_SOURCE"},
        "B": {"relay": "B:DEMO_RELAY"},
        "C": {"receiver": "C:DEMO_RECEIVER"},
    }

    events = build_heist_events(accounts)

    assert [event["bank"] for event in events] == ["A", "B", "C"]
    assert events[1]["src_account"] == events[0]["dst_account"]
    assert events[2]["src_account"] == events[1]["dst_account"]
    assert events[1]["ts_ms"] < events[2]["ts_ms"]
    assert events[1]["dst_account"] == accounts["C"]["receiver"]


def test_run_heist_routes_each_event_to_its_bank():
    accounts = {
        "A": {"source": "A:DEMO_SOURCE"},
        "B": {"relay": "B:DEMO_RELAY"},
        "C": {"receiver": "C:DEMO_RECEIVER"},
    }
    calls = []

    class Response:
        status_code = 200
        ok = True

        def raise_for_status(self):
            pass

        def json(self):
            return {"accepted": True}

    def post(url, *, json, timeout):
        calls.append((url, json, timeout))
        return Response()

    results = run_heist(
        accounts,
        {bank: f"http://bank-{bank}" for bank in ("A", "B", "C")},
        post=post,
        sleep_fn=lambda _: None,
    )

    assert [call[0] for call in calls] == [
        "http://bank-A/event",
        "http://bank-B/event",
        "http://bank-C/event",
    ]
    assert all(result["accepted"] for result in results)
    assert all("DEMO_SOURCE" not in repr(result) for result in results)

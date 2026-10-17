from healer.prometheus import QUERIES, PrometheusClient


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class FakeSession:
    """Answers each PromQL query with canned results."""

    def __init__(self, answers):
        self.answers = answers
        self.queries = []

    def get(self, url, params, timeout):
        q = params["query"]
        self.queries.append(q)
        name = next(k for k, v in QUERIES.items() if v == q)
        result = [{"metric": {"application": app}, "value": [0, str(val)]}
                  for app, val in self.answers.get(name, {}).items()]
        return FakeResponse({"status": "success", "data": {"resultType": "vector", "result": result}})


def test_collect_builds_one_sample_per_service():
    session = FakeSession({
        "latency_p95_ms": {"order-service": 52.0, "payment-service": 31.0},
        "error_rate": {"payment-service": 0.25},
        "heap_ratio": {"order-service": 0.3, "payment-service": 0.4},
        "request_rate": {"order-service": 20, "payment-service": 20},
    })
    client = PrometheusClient("http://prom:9090", session=session)

    samples = client.collect(["order-service", "payment-service", "missing-service"])

    assert set(samples) == {"order-service", "payment-service"}
    assert samples["payment-service"].error_rate == 0.25
    # order-service had no 5xx series, which means zero errors.
    assert samples["order-service"].error_rate == 0.0
    assert len(session.queries) == len(QUERIES)


def test_nan_values_are_dropped():
    session = FakeSession({"latency_p95_ms": {"svc": "NaN"}, "request_rate": {"svc": 1}})
    samples = PrometheusClient("http://prom", session=session).collect(["svc"])
    assert samples["svc"].latency_p95_ms == 0.0


def test_queries_exclude_actuator_and_chaos_traffic():
    for name in ("latency_p95_ms", "error_rate", "request_rate"):
        assert '/actuator.*|/chaos.*' in QUERIES[name]

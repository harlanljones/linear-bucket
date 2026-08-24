import json
import unittest

from parent_progress_sync.linear_client import (
    LinearAPIError,
    LinearClient,
    RateLimitError,
    Response,
)


def ok(data):
    return Response(200, {}, json.dumps({"data": data}))


class RecordingTransport:
    """Replays a queued list of responses and records the requests made."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def __call__(self, url, headers, body):
        self.requests.append((url, dict(headers), json.loads(body.decode("utf-8"))))
        return self._responses.pop(0)


class ExecuteTests(unittest.TestCase):
    def build(self, responses, **kwargs):
        self.transport = RecordingTransport(responses)
        self.slept = []
        return LinearClient(
            api_key="lin_api_test",
            api_url="https://api.linear.app/graphql",
            transport=self.transport,
            sleep=self.slept.append,
            **kwargs,
        )

    def test_sends_authorized_query(self):
        client = self.build([ok({"viewer": {"id": "u1"}})])

        self.assertEqual(client.execute("query { viewer { id } }"), {"viewer": {"id": "u1"}})

        url, headers, payload = self.transport.requests[0]
        self.assertEqual(url, "https://api.linear.app/graphql")
        self.assertEqual(headers["Authorization"], "lin_api_test")
        self.assertEqual(payload["query"], "query { viewer { id } }")

    def test_retries_http_429_and_honours_retry_after(self):
        client = self.build(
            [Response(429, {"Retry-After": "3"}, ""), ok({"viewer": {"id": "u1"}})]
        )

        client.execute("query { viewer { id } }")

        self.assertEqual(self.slept, [3.0])
        self.assertEqual(len(self.transport.requests), 2)

    def test_retries_graphql_ratelimited_with_exponential_backoff(self):
        rate_limited = Response(
            200, {}, json.dumps({"errors": [{"message": "slow down", "extensions": {"code": "RATELIMITED"}}]})
        )
        client = self.build([rate_limited, rate_limited, ok({"viewer": {"id": "u1"}})])

        client.execute("query { viewer { id } }")

        self.assertEqual(self.slept, [1.0, 2.0])

    def test_gives_up_after_max_retries(self):
        client = self.build([Response(503, {}, "")] * 3, max_retries=2)

        with self.assertRaises(RateLimitError):
            client.execute("query { viewer { id } }")
        self.assertEqual(len(self.transport.requests), 3)

    def test_graphql_errors_are_not_retried(self):
        client = self.build([Response(200, {}, json.dumps({"errors": [{"message": "nope"}]}))])

        with self.assertRaises(LinearAPIError):
            client.execute("query { viewer { id } }")
        self.assertEqual(len(self.transport.requests), 1)

    def test_client_errors_surface(self):
        client = self.build([Response(401, {}, "unauthorized")])

        with self.assertRaises(LinearAPIError):
            client.execute("query { viewer { id } }")


class PaginateTests(unittest.TestCase):
    def test_follows_cursors_until_exhausted(self):
        page_one = ok(
            {
                "issues": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                    "nodes": [{"id": "a"}, {"id": "b"}],
                }
            }
        )
        page_two = ok(
            {
                "issues": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{"id": "c"}],
                }
            }
        )
        transport = RecordingTransport([page_one, page_two])
        client = LinearClient("k", "u", transport=transport, sleep=lambda _: None)

        nodes = list(client.paginate("query {}", {}, ("issues",), page_size=2))

        self.assertEqual([node["id"] for node in nodes], ["a", "b", "c"])
        self.assertIsNone(transport.requests[0][2]["variables"]["after"])
        self.assertEqual(transport.requests[1][2]["variables"]["after"], "cursor-1")
        self.assertEqual(transport.requests[1][2]["variables"]["first"], 2)

    def test_nested_path(self):
        response = ok(
            {
                "issue": {
                    "children": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [{"id": "child"}],
                    }
                }
            }
        )
        client = LinearClient("k", "u", transport=RecordingTransport([response]), sleep=lambda _: None)

        nodes = list(client.paginate("query {}", {"id": "x"}, ("issue", "children")))

        self.assertEqual([node["id"] for node in nodes], ["child"])

    def test_missing_path_raises(self):
        client = LinearClient(
            "k", "u", transport=RecordingTransport([ok({})]), sleep=lambda _: None
        )

        with self.assertRaises(LinearAPIError):
            list(client.paginate("query {}", {}, ("issues",)))


if __name__ == "__main__":
    unittest.main()

import json
import unittest
import urllib.error
import urllib.request

import remote_api


class RemoteApiMarkerTests(unittest.TestCase):
    def setUp(self):
        self.state = remote_api.RemoteControlState()
        self.server = remote_api.start_server(self.state, 0)
        self.assertIsNotNone(self.server)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_mark_endpoint_queues_custom_label(self):
        request = urllib.request.Request(
            f"{self.url}/mark",
            data=json.dumps({"label": "Round win"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.read())["ok"])

        self.assertEqual(self.state.consume_timeline_markers(), ["Round win"])

    def test_mark_endpoint_rejects_invalid_json(self):
        request = urllib.request.Request(
            f"{self.url}/mark",
            data=b"{",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)

        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.state.consume_timeline_markers(), [])


if __name__ == "__main__":
    unittest.main()

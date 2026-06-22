import socket
import ssl
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import monitor


NOW = datetime(2026, 6, 22, 5, 30, tzinfo=timezone.utc)


class RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, _format, *_args):
        pass


class AssessmentTests(unittest.TestCase):
    def test_fast_2xx_is_healthy(self):
        result = monitor.assess_http(200, 4.99)
        self.assertTrue(result.healthy)

    def test_slow_2xx_is_unhealthy(self):
        result = monitor.assess_http(200, 5.0)
        self.assertFalse(result.healthy)
        self.assertIn("응답 지연", result.reason)

    def test_final_non_2xx_is_unhealthy(self):
        result = monitor.assess_http(503, 0.2)
        self.assertEqual(result.reason, "HTTP 503")

    def test_redirected_final_2xx_is_healthy(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            result = monitor.check_site(
                f"http://127.0.0.1:{server.server_port}/redirect"
            )
            self.assertTrue(result.healthy)
            self.assertEqual(result.status_code, 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_network_error_classification(self):
        cases = [
            (URLError(socket.gaierror()), "DNS 조회 실패"),
            (URLError(ConnectionRefusedError()), "TCP 연결 실패"),
            (URLError(ssl.SSLError()), "TLS/인증서 오류"),
            (URLError(socket.timeout()), "응답 시간 초과"),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, monitor.classify_network_error(error))


class StateTransitionTests(unittest.TestCase):
    def setUp(self):
        self.failure = monitor.CheckResult(False, "HTTP 503", 503, 0.5)
        self.success = monitor.CheckResult(True, "정상", 200, 0.4)

    def test_first_failure_alerts_immediately(self):
        message, state = monitor.decide_notification(self.failure, None, NOW)
        self.assertIn("홈페이지 장애", message)
        self.assertEqual(state.status, "unhealthy")
        self.assertEqual(state.last_alert_at, NOW.isoformat())

    def test_failure_before_30_minutes_is_suppressed(self):
        state = monitor.MonitorState("unhealthy", NOW.isoformat(), NOW.isoformat())
        message, next_state = monitor.decide_notification(
            self.failure, state, NOW + timedelta(minutes=29)
        )
        self.assertIsNone(message)
        self.assertEqual(next_state, state)

    def test_failure_at_30_minutes_sends_reminder(self):
        state = monitor.MonitorState("unhealthy", NOW.isoformat(), NOW.isoformat())
        message, next_state = monitor.decide_notification(
            self.failure, state, NOW + timedelta(minutes=30)
        )
        self.assertIn("장애 지속", message)
        self.assertEqual(next_state.last_alert_at, (NOW + timedelta(minutes=30)).isoformat())

    def test_recovery_alert_contains_duration(self):
        state = monitor.MonitorState("unhealthy", NOW.isoformat(), NOW.isoformat())
        message, next_state = monitor.decide_notification(
            self.success, state, NOW + timedelta(minutes=12)
        )
        self.assertIn("홈페이지 복구", message)
        self.assertIn("12분", message)
        self.assertEqual(next_state, monitor.MonitorState("healthy"))

    def test_send_failure_does_not_commit_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"

            def fail(_message):
                raise RuntimeError("send failed")

            with self.assertRaises(RuntimeError):
                monitor.run_monitor(self.failure, path, NOW, fail)
            self.assertFalse(path.exists())

    def test_initial_success_saves_baseline_without_notification(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            messages = []
            changed = monitor.run_monitor(self.success, path, NOW, messages.append)
            self.assertTrue(changed)
            self.assertEqual(messages, [])
            self.assertEqual(monitor.load_state(path), monitor.MonitorState("healthy"))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Monitor the website and send state-change notifications to Telegram."""

from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from dotenv import load_dotenv

load_dotenv()

TARGET_URL = os.environ.get("TARGET_URL", "").strip()
SLOW_THRESHOLD_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 10.0
REMINDER_INTERVAL = timedelta(minutes=30)
STATE_PATH = Path(".monitor-state.json")
KST = timezone(timedelta(hours=9), name="KST")


@dataclass(frozen=True)
class CheckResult:
    healthy: bool
    reason: str
    status_code: int | None
    elapsed_seconds: float


@dataclass(frozen=True)
class MonitorState:
    status: str
    incident_started_at: str | None = None
    last_alert_at: str | None = None


def assess_http(status_code: int, elapsed_seconds: float) -> CheckResult:
    if not 200 <= status_code < 300:
        return CheckResult(False, f"HTTP {status_code}", status_code, elapsed_seconds)
    if elapsed_seconds >= SLOW_THRESHOLD_SECONDS:
        return CheckResult(
            False,
            f"응답 지연 ({elapsed_seconds:.2f}초, 기준 {SLOW_THRESHOLD_SECONDS:.0f}초)",
            status_code,
            elapsed_seconds,
        )
    return CheckResult(True, "정상", status_code, elapsed_seconds)


def classify_network_error(error: BaseException) -> str:
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, socket.gaierror):
        return "DNS 조회 실패"
    if isinstance(reason, (ssl.SSLError, ssl.CertificateError)):
        return "TLS/인증서 오류"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return f"응답 시간 초과 ({REQUEST_TIMEOUT_SECONDS:.0f}초)"

    message = str(reason).lower()
    if "name or service not known" in message or "getaddrinfo" in message:
        return "DNS 조회 실패"
    if "ssl" in message or "certificate" in message or "tls" in message:
        return "TLS/인증서 오류"
    if "timed out" in message:
        return f"응답 시간 초과 ({REQUEST_TIMEOUT_SECONDS:.0f}초)"
    return "TCP 연결 실패"


def check_site(url: str | None = None) -> CheckResult:
    target_url = url or TARGET_URL
    if not target_url:
        raise ValueError("TARGET_URL이 필요합니다.")
    request = Request(target_url, headers={"User-Agent": "newyjh-uptime-monitor/1.0"})
    started = time.monotonic()
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            response.read()
            elapsed = time.monotonic() - started
            return assess_http(response.status, elapsed)
    except HTTPError as error:
        elapsed = time.monotonic() - started
        return CheckResult(False, f"HTTP {error.code}", error.code, elapsed)
    except (URLError, OSError, TimeoutError) as error:
        elapsed = time.monotonic() - started
        return CheckResult(False, classify_network_error(error), None, elapsed)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def format_kst(value: datetime) -> str:
    return value.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def format_duration(duration: timedelta) -> str:
    total_minutes = max(0, int(duration.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}시간 {minutes}분"
    return f"{minutes}분"


def result_details(result: CheckResult) -> str:
    status = str(result.status_code) if result.status_code is not None else "없음"
    return (
        f"URL: {TARGET_URL}\n"
        f"원인: {result.reason}\n"
        f"HTTP 상태: {status}\n"
        f"응답 시간: {result.elapsed_seconds:.2f}초"
    )


def decide_notification(
    result: CheckResult, state: MonitorState | None, now: datetime
) -> tuple[str | None, MonitorState]:
    now_text = now.isoformat()
    if result.healthy:
        next_state = MonitorState("healthy")
        if state and state.status == "unhealthy":
            started = parse_time(state.incident_started_at or now_text)
            message = (
                "🟢 홈페이지 복구\n"
                f"시간: {format_kst(now)}\n"
                f"장애 지속: {format_duration(now - started)}\n"
                f"URL: {TARGET_URL}\n"
                f"HTTP 상태: {result.status_code}\n"
                f"응답 시간: {result.elapsed_seconds:.2f}초"
            )
            return message, next_state
        return None, next_state

    if not state or state.status != "unhealthy":
        next_state = MonitorState("unhealthy", now_text, now_text)
        message = (
            "🔴 홈페이지 장애\n"
            f"시간: {format_kst(now)}\n"
            f"{result_details(result)}"
        )
        return message, next_state

    last_alert = parse_time(state.last_alert_at or state.incident_started_at or now_text)
    if now - last_alert >= REMINDER_INTERVAL:
        started_text = state.incident_started_at or now_text
        started = parse_time(started_text)
        next_state = MonitorState("unhealthy", started_text, now_text)
        message = (
            "🔴 홈페이지 장애 지속\n"
            f"시간: {format_kst(now)}\n"
            f"장애 지속: {format_duration(now - started)}\n"
            f"{result_details(result)}"
        )
        return message, next_state

    return None, state


def load_state(path: Path) -> MonitorState | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return MonitorState(**data)


def save_state(path: Path, state: MonitorState) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def send_telegram(message: str, token: str, chat_id: str) -> None:
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = Request(endpoint, data=body, method="POST")
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, TimeoutError) as error:
        raise RuntimeError("텔레그램 API 요청에 실패했습니다.") from error
    if not payload.get("ok"):
        raise RuntimeError("텔레그램 API가 메시지 전송을 거부했습니다.")


def run_monitor(
    result: CheckResult,
    state_path: Path,
    now: datetime,
    notify: Callable[[str], None],
) -> bool:
    previous = load_state(state_path)
    message, next_state = decide_notification(result, previous, now)
    if message:
        notify(message)
    if next_state != previous:
        save_state(state_path, next_state)
        return True
    return False


def write_github_output(changed: bool) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"state_changed={'true' if changed else 'false'}\n")


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID가 필요합니다.", file=sys.stderr)
        return 1
    if not TARGET_URL:
        print("TARGET_URL GitHub Actions 변수가 필요합니다.", file=sys.stderr)
        return 1

    try:
        if os.environ.get("SEND_TEST_NOTIFICATION", "false").lower() == "true":
            send_telegram(
                "🧪 홈페이지 모니터링 테스트\n"
                f"시간: {format_kst(datetime.now(timezone.utc))}\n"
                f"URL: {TARGET_URL}\n"
                "텔레그램 알림 설정이 정상입니다.",
                token,
                chat_id,
            )
        result = check_site()
        changed = run_monitor(
            result,
            STATE_PATH,
            datetime.now(timezone.utc),
            lambda message: send_telegram(message, token, chat_id),
        )
        write_github_output(changed)
        print(f"상태: {result.reason}, 응답 시간: {result.elapsed_seconds:.2f}초")
        return 0
    except (json.JSONDecodeError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"모니터 실행 실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

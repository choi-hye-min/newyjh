# newyjh 홈페이지 모니터

`https://www.newyjh.com/main/index.htm`을 GitHub Actions에서 5분마다 검사하고 장애를 텔레그램으로 알립니다.

## 판정 및 알림 기준

- 리다이렉트를 따라간 최종 응답이 HTTP 2xx이고 5초 미만이면 정상입니다.
- HTTP 오류, DNS·TCP·TLS 오류, 10초 타임아웃 또는 5초 이상 응답 지연은 장애입니다.
- 첫 장애는 즉시 알리고, 장애가 계속되면 30분마다 다시 알립니다.
- 장애 후 정상화되면 복구 시간과 장애 지속 시간을 알립니다.
- GitHub Actions의 예약 실행은 플랫폼 상황에 따라 몇 분 늦어질 수 있습니다.

## 텔레그램 설정

1. 텔레그램에서 [@BotFather](https://t.me/BotFather)를 열고 `/newbot`으로 봇을 만든 뒤 발급된 토큰을 보관합니다.
2. 만든 봇과 대화를 열고 `/start`를 보냅니다. 그룹에서 받을 경우 봇을 해당 그룹에 추가하고 메시지를 하나 보냅니다.
3. 브라우저에서 아래 주소를 열어 `result` 안의 `chat.id` 값을 확인합니다.

   ```text
   https://api.telegram.org/bot<발급받은_토큰>/getUpdates
   ```

4. GitHub 저장소의 **Settings → Secrets and variables → Actions → New repository secret**에서 다음 두 값을 등록합니다.

   - `TELEGRAM_BOT_TOKEN`: BotFather가 발급한 토큰
   - `TELEGRAM_CHAT_ID`: 위에서 확인한 개인 또는 그룹의 `chat.id`

토큰은 README, 소스 코드, Actions 로그에 직접 입력하지 마세요. 그룹 chat ID는 보통 음수입니다.

## 실행과 확인

변경 사항을 GitHub 기본 브랜치에 push하면 예약 실행이 활성화됩니다. 저장소의 **Actions → Website monitor → Run workflow**에서 즉시 실행할 수도 있습니다.

실제 텔레그램 수신까지 확인하려면 `Send a Telegram test message`를 선택하고 실행합니다. 테스트 메시지를 보낸 뒤 같은 실행에서 홈페이지 상태도 검사합니다. 정상 상태의 첫 검사는 기준 상태만 저장하므로 장애 메시지를 보내지 않습니다.

로컬 단위 테스트는 별도 패키지 설치 없이 실행할 수 있습니다.

```powershell
python -m unittest -v
```

로컬에서 `monitor.py`를 직접 실행하려면 비밀값을 환경 변수로 설정해야 하며, `.monitor-state.json`에 이전 상태가 저장됩니다.

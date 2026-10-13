# AI 뉴스 브리핑

매일 08:00·20:00에 최근 인공지능 주요 기사(국내+해외)와 그 밖의 화제 기사를 모아 Claude로 요약하고,
PDF로 만들어 Gmail로 보냅니다. PDF 구성: ① 용어 정리 → ② 인공지능 주요 기사 → ③ 그 밖의 화제 기사.

## 설치
1. 패키지 설치: `python -m pip install -r requirements.txt`
2. `.env.example`을 `.env`로 복사한 뒤 값을 채웁니다.
   - **SUMMARIZER**: 기본 `claude-code` — 이 PC에 로그인된 Claude Code(구독)로 요약하므로 API 키가 필요 없음.
     `api`로 바꾸면 **ANTHROPIC_API_KEY**(console.anthropic.com, 크레딧 충전 필요)로 요약
   - **GMAIL_APP_PASSWORD**: Google 계정 → 보안 → 2단계 인증 사용 → https://myaccount.google.com/apppasswords 에서 16자리 앱 비밀번호 생성
   - **CLAUDE_MODEL**: 기본 `claude-opus-5-5`. 비용을 줄이려면 `claude-sonnet-5-5` 또는 `claude-haiku-5-5`
3. 시험 실행
   - `python news_brief.py --dry-run` → `reports/` 폴더의 PDF 확인 (메일 발송 안 함)
   - `python news_brief.py` → 실제 메일 발송
4. 자동 실행 등록: `powershell -ExecutionPolicy Bypass -File .\register_task.ps1`

## 참고
- 로그: `logs/YYYY-MM.log`, 생성된 PDF: `reports/`
- PC가 08:00/20:00에 꺼져 있었으면 다음에 켜질 때 실행됩니다.
- 요약에 실패하면 수집한 헤드라인 목록만 담은 PDF를 보냅니다.
- 작업 삭제: `Unregister-ScheduledTask -TaskName 'AI 뉴스 브리핑 (오전)','AI 뉴스 브리핑 (오후)'`

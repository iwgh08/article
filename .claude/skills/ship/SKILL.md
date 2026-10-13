---
name: ship
description: 변경 사항을 점검한 뒤 git add / commit / push 한다. 사용자가 /ship 을 입력했을 때만 실행.
disable-model-invocation: true
---

# /ship — 점검 후 커밋·푸시

아래 단계를 순서대로 진행한다. **어느 단계든 문제가 발견되면 즉시 멈추고**, 무엇이 문제인지와 해결 방법을 한국어로 알려준다. 문제가 있는 상태로 커밋하거나 푸시하지 않는다.

## 1. 저장소 상태 확인
- `git rev-parse --is-inside-work-tree`로 git 저장소인지 확인한다. 아니면 멈추고 `git init`을 제안한다.
- `git status --short`와 `git diff --stat`으로 변경 사항을 확인한다. 변경 사항이 없으면 "올릴 변경 사항이 없습니다"라고 알리고 끝낸다.
- 현재 브랜치(`git branch --show-current`)와 원격 저장소(`git remote -v`)를 확인해 둔다.

## 2. 비밀 정보 점검 (가장 중요)
- `.env`가 `.gitignore`에 들어 있고 `git ls-files .env`가 비어 있는지 확인한다. 이미 추적 중이면 멈추고 `git rm --cached .env`를 안내한다.
- 커밋될 변경 내용(추적되지 않은 새 파일 포함)에서 다음 패턴을 검사한다:
  `sk-ant-`, `GMAIL_APP_PASSWORD=` 뒤에 값이 있는 줄, `ANTHROPIC_API_KEY=` 뒤에 값이 있는 줄, 16자리 앱 비밀번호처럼 보이는 문자열, `password`/`secret`/`token`에 실제 값이 대입된 줄.
  `.env.example`의 빈 값이나 `sk-ant-...` 같은 예시 표시는 문제가 아니다.
- `logs/`, `reports/`, `__pycache__/` 파일이 포함되지 않았는지 확인한다.

## 3. 코드 점검
- `python -m py_compile news_brief.py` — 문법 오류 확인
- `python -c "import news_brief"` — import 오류 확인 (실제 요약·발송은 하지 않음)
- 변경된 `.ps1` 파일이 있으면 PowerShell 파서로 문법을 확인한다:
  `powershell -NoProfile -Command "$e=$null; [Management.Automation.Language.Parser]::ParseFile('<파일>', [ref]$null, [ref]$e) | Out-Null; $e.Count"` → 0이어야 함
- 변경 내용(`git diff`)을 읽고 명백한 버그, 디버그용 코드, 깨진 한글 등이 없는지 훑어본다. 심각한 문제는 멈추고 알리고, 사소한 것은 커밋 전에 짧게 언급만 한다.
- `python news_brief.py --dry-run` 같은 실제 실행은 하지 않는다(시간이 걸리고 구독 사용량을 씀). 사용자가 원하면 그때 한다.

## 4. 커밋
- `git add -A`로 스테이징한 뒤 `git status --short`로 무엇이 들어가는지 다시 확인한다(2단계 점검 대상이 섞이지 않았는지).
- 변경 내용을 요약한 **한국어 커밋 메시지**를 작성한다. 첫 줄은 50자 안팎의 요약, 필요하면 빈 줄 뒤에 상세 내용.
- 커밋 메시지 끝에는 이 세션의 커밋 attribution 규칙이 있으면 그대로 따른다.
- `git commit`으로 커밋한다. 훅이 실패하면 `--no-verify`로 우회하지 말고 원인을 고친다.

## 5. 푸시
- 원격 저장소가 없으면 커밋까지만 하고 멈춘 뒤, GitHub 등에 저장소를 만들고 `git remote add origin <URL>`을 하라고 안내한다.
- 업스트림이 없으면 `git push -u origin <현재 브랜치>`, 있으면 `git push`.
- 푸시가 거절되면(원격에 새 커밋 등) 강제 푸시하지 말고 상황을 알린다.

## 6. 결과 보고
짧게 알려준다: 점검 결과(통과/주의 사항), 커밋 해시와 메시지 첫 줄, 푸시한 원격·브랜치(또는 푸시하지 못한 이유).

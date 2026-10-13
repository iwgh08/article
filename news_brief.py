"""AI 뉴스 브리핑: RSS로 기사를 모아 Claude로 요약하고, PDF로 만들어 Gmail로 보낸다.

사용법:
    python news_brief.py            # 수집 → 요약 → PDF → 메일 발송
    python news_brief.py --dry-run  # 메일은 보내지 않고 PDF만 생성
"""

import argparse
import calendar
import html
import json
import logging
import os
import re
import shutil
import smtplib
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import anthropic
import feedparser
from dotenv import load_dotenv
from fpdf import FPDF

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
REPORT_DIR = BASE_DIR / "reports"
KST = timezone(timedelta(hours=9))

FONT_REGULAR = r"C:\Windows\Fonts\malgun.ttf"
FONT_BOLD = r"C:\Windows\Fonts\malgunbd.ttf"

AI_FEEDS = [
    "https://news.google.com/rss/search?q=%EC%9D%B8%EA%B3%B5%EC%A7%80%EB%8A%A5+OR+AI+when:1d&hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=%22artificial+intelligence%22+OR+OpenAI+OR+Anthropic+when:1d&hl=en-US&gl=US&ceid=US:en",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
]
TRENDING_FEEDS = [
    "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
]

MAX_CANDIDATES_PER_FEED = 20
# 서버 측 refusal fallback("default")을 지원하는 모델
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}

log = logging.getLogger("news_brief")


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"{datetime.now(KST):%Y-%m}.log"
    handlers = [logging.FileHandler(log_file, encoding="utf-8")]
    if sys.stdout is not None:  # pythonw 실행 시 stdout이 없음
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )
    logging.getLogger("fontTools").setLevel(logging.ERROR)  # PDF 폰트 처리 로그 숨김


# ---------------------------------------------------------------- 1. 수집

def clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fetch_feed(url: str, since: datetime) -> list[dict]:
    parsed = feedparser.parse(url, agent="Mozilla/5.0 (news-brief)")
    if parsed.bozo and not parsed.entries:
        log.warning("피드 읽기 실패: %s (%s)", url, parsed.bozo_exception)
        return []

    feed_title = clean_text(parsed.feed.get("title", ""))
    items = []
    for entry in parsed.entries:
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        if stamp:
            published = datetime.fromtimestamp(calendar.timegm(stamp), tz=timezone.utc)
            if published < since:
                continue
        else:
            published = None

        title = clean_text(entry.get("title", ""))
        source = clean_text(entry.get("source", {}).get("title", "")) or feed_title
        # Google News 제목 끝의 " - 매체명" 제거
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        summary = clean_text(entry.get("summary", ""))
        if summary.startswith(title):  # Google News는 발췌문이 제목의 반복인 경우가 많음
            summary = ""

        items.append({
            "title": title,
            "source": source,
            "url": entry.get("link", ""),
            "snippet": summary[:300],
            "published": published.astimezone(KST).strftime("%m-%d %H:%M") if published else "",
        })
        if len(items) >= MAX_CANDIDATES_PER_FEED:
            break
    return items


def collect(feeds: list[str], prefix: str, hours: int) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    seen, result = set(), []
    for url in feeds:
        try:
            items = fetch_feed(url, since)
        except Exception:
            log.exception("피드 처리 중 오류: %s", url)
            continue
        for item in items:
            key = re.sub(r"\W", "", item["title"].lower())[:60]
            if not key or key in seen:
                continue
            seen.add(key)
            item["id"] = f"{prefix}{len(result) + 1}"
            result.append(item)
    return result


def collect_all() -> tuple[list[dict], list[dict]]:
    ai = collect(AI_FEEDS, "A", hours=12)
    if len(ai) < 10:
        log.info("최근 12시간 AI 기사가 %d건뿐이라 24시간으로 넓힘", len(ai))
        ai = collect(AI_FEEDS, "A", hours=24)
    trending = collect(TRENDING_FEEDS, "T", hours=24)
    log.info("후보 수집: AI %d건, 화제 %d건", len(ai), len(trending))
    return ai, trending


# ---------------------------------------------------------------- 2. 요약

SYSTEM_PROMPT = """너는 한국어 뉴스 브리핑 편집자다. 독자는 IT 전문가가 아닌 일반인이다.
주어진 후보 기사 목록에서 기사를 고르고, 필요하면 웹 검색으로 세부 내용을 확인한 뒤 한국어로 요약한다.
사실 확인이 안 되는 내용은 추측하지 말고 확인된 내용만 쓴다."""

USER_PROMPT_TEMPLATE = """오늘은 {now} (한국시간)이다. 아래 후보 기사로 브리핑을 만들어라.

## 작업
1. AI 후보(A로 시작하는 id) 중 가장 중요하고 영향력 있는 AI 기사 5~7개를 고른다. 같은 사건을 다룬 기사는 하나로 합친다. 국내와 해외 기사를 고르게 섞는다.
2. 화제 후보(T로 시작하는 id) 중 AI와 관계없이 지금 가장 화제가 되는 기사 3~5개를 고른다. AI 기사와 겹치지 않게 한다.
3. 고른 기사의 내용이 제목만으로 부족하면 web_search로 확인한다(꼭 필요한 경우만).
4. 각 기사마다 한국어 제목과 3~5문장 요약을 쓴다. 무엇이 일어났는지, 왜 중요한지를 담는다.
5. 요약에 등장하는 어려운 용어(기술 용어, 영어 약어, 경제·법률 용어 등)를 모아 쉬운 말로 1~2문장씩 설명한다. 5~10개.

## 출력 형식
마지막에 아래 JSON만 <json>과 </json> 태그 사이에 출력한다. id는 반드시 후보 목록의 id를 그대로 쓴다.
<json>
{{
  "terms": [{{"term": "용어", "explanation": "쉬운 설명"}}],
  "ai_articles": [{{"id": "A3", "title": "한국어 제목", "summary": "요약"}}],
  "trending": [{{"id": "T2", "title": "한국어 제목", "summary": "요약"}}]
}}
</json>

## AI 후보
{ai_list}

## 화제 후보
{trending_list}
"""


def format_candidates(items: list[dict]) -> str:
    lines = []
    for it in items:
        line = f"[{it['id']}] {it['title']} ({it['source']}, {it['published']})"
        if it["snippet"]:
            line += f"\n    {it['snippet']}"
        lines.append(line)
    return "\n".join(lines) or "(없음)"


def extract_json(text: str) -> dict:
    match = re.search(r"<json>(.*?)</json>", text, re.S)
    body = match.group(1) if match else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(body)


def find_claude_cli() -> str:
    path = shutil.which("claude")
    if path:
        return path
    fallback = Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"
    if fallback.exists():
        return str(fallback)
    raise RuntimeError("claude CLI를 찾을 수 없습니다. Claude Code가 설치되어 있는지 확인하세요.")


def run_claude_code(model: str, user_prompt: str) -> str:
    """구독 계정으로 로그인된 Claude Code CLI(claude -p)로 요약한다."""
    env = os.environ.copy()
    # API 키가 있으면 CLI가 구독 대신 API 크레딧을 쓰므로 제거
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)

    cmd = [
        find_claude_cli(), "-p",
        "--model", model,
        "--output-format", "json",
        "--tools", "WebSearch",
        "--allowedTools", "WebSearch",
        "--append-system-prompt", SYSTEM_PROMPT,
    ]
    proc = subprocess.run(
        cmd, input=user_prompt, capture_output=True, text=True, encoding="utf-8",
        env=env, cwd=BASE_DIR, timeout=1200,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(f"claude CLI 실패 (code {proc.returncode}): {proc.stderr[-2000:]}")

    result = json.loads(proc.stdout)
    log.info("Claude Code 응답: subtype=%s turns=%s 소요=%.0f초",
             result.get("subtype"), result.get("num_turns"), result.get("duration_ms", 0) / 1000)
    if result.get("is_error"):
        raise RuntimeError(f"claude CLI 오류: {result.get('result') or result.get('subtype')}")
    return result.get("result", "")


def run_api(model: str, user_prompt: str) -> str:
    """Anthropic API(크레딧 차감)로 요약한다."""
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": user_prompt}]

    params = dict(
        model=model,
        max_tokens=32000,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}],
        output_config={"effort": "medium"},
    )
    if model in FALLBACK_MODELS:
        params["betas"] = ["server-side-fallback-2026-07-01"]
        params["fallbacks"] = "default"

    response = None
    for _ in range(5):  # pause_turn 이어가기 횟수 제한
        with client.beta.messages.stream(messages=messages, **params) as stream:
            response = stream.get_final_message()
        if response.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": response.content}]

    log.info("Claude 응답: model=%s stop_reason=%s usage=%s",
             response.model, response.stop_reason, response.usage)
    if response.stop_reason == "refusal":
        raise RuntimeError(f"요약 요청이 거절됨: {response.stop_details}")

    return "".join(b.text for b in response.content if b.type == "text")


def summarize(ai: list[dict], trending: list[dict]) -> dict:
    model = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
    backend = os.getenv("SUMMARIZER", "claude-code")
    user_prompt = USER_PROMPT_TEMPLATE.format(
        now=datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        ai_list=format_candidates(ai),
        trending_list=format_candidates(trending),
    )
    log.info("요약 시작: backend=%s model=%s", backend, model)
    if backend == "api":
        text = run_api(model, user_prompt)
    else:
        text = run_claude_code(model, user_prompt)
    data = extract_json(text)

    # id를 원래 기사의 링크·매체와 연결 (모델이 URL을 지어내지 않도록)
    by_id = {it["id"]: it for it in ai + trending}
    for key in ("ai_articles", "trending"):
        merged = []
        for art in data.get(key, []):
            src = by_id.get(art.get("id"))
            if not src:
                log.warning("알 수 없는 기사 id: %s", art.get("id"))
                continue
            merged.append({**art, "source": src["source"], "url": src["url"]})
        data[key] = merged
    return data


def headline_only(ai: list[dict], trending: list[dict]) -> dict:
    """요약 실패 시 헤드라인 목록만으로 브리핑을 만든다."""
    def pick(items, n):
        return [{"title": it["title"], "source": it["source"], "url": it["url"],
                 "summary": it["snippet"]} for it in items[:n]]
    return {"terms": [], "ai_articles": pick(ai, 10), "trending": pick(trending, 5),
            "notice": "요약 생성에 실패하여 수집된 헤드라인만 보냅니다. 자세한 내용은 logs 폴더를 확인하세요."}


# ---------------------------------------------------------------- 3. PDF

# 어두운 배경(눈 보호용) 색상
BG = (18, 18, 20)
TEXT = (225, 225, 225)
MUTED = (150, 150, 155)
ACCENT = (130, 180, 255)
LINK = (110, 200, 255)
WARN = (255, 130, 120)


def line_h(size: float) -> float:
    """글자 크기(pt)에 맞는 줄 높이(mm)."""
    return size * 0.56


class BriefPDF(FPDF):
    def header(self):
        # 모든 페이지 배경을 어둡게 칠함
        self.set_fill_color(*BG)
        self.rect(0, 0, self.w, self.h, "F")

    def footer(self):
        self.set_y(-14)
        self.set_font("Malgun", size=10)
        self.set_text_color(*MUTED)
        self.cell(0, 8, f"- {self.page_no()} -", align="C")


def build_pdf(data: dict, title: str, out_path: Path) -> None:
    pdf = BriefPDF(format="A4")
    pdf.add_font("Malgun", "", FONT_REGULAR)
    pdf.add_font("Malgun", "B", FONT_BOLD)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(16, 18, 16)
    pdf.add_page()

    def text(content: str, size: float, color, bold: bool = False, indent: float = 0) -> None:
        pdf.set_font("Malgun", "B" if bold else "", size)
        pdf.set_text_color(*color)
        pdf.set_x(pdf.l_margin + indent)
        pdf.multi_cell(0, line_h(size), content, new_x="LMARGIN", new_y="NEXT", align="L")

    text(title, 24, ACCENT, bold=True)
    text(f"생성: {datetime.now(KST):%Y-%m-%d %H:%M} KST", 11, MUTED)
    pdf.ln(5)

    if data.get("notice"):
        text(data["notice"], 12.5, WARN, bold=True)
        pdf.ln(4)

    def keep_space(height: float) -> None:
        """남은 공간이 부족하면 다음 쪽에서 시작 (제목만 홀로 남지 않도록)."""
        if pdf.will_page_break(height):
            pdf.add_page()

    def section(heading: str) -> None:
        keep_space(45)
        pdf.ln(3)
        text(heading, 18, ACCENT, bold=True)
        y = pdf.get_y() + 1
        pdf.set_draw_color(*ACCENT)
        pdf.set_line_width(0.4)
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(5)

    if data.get("terms"):
        section("① 먼저 알아두면 좋은 용어")
        for t in data["terms"]:
            keep_space(20)
            text(f"· {t['term']}", 14, TEXT, bold=True)
            text(t["explanation"], 13, MUTED, indent=5)
            pdf.ln(2.5)

    def articles(items: list[dict]) -> None:
        for i, art in enumerate(items, 1):
            keep_space(35)
            text(f"{i}. {art['title']}", 15, TEXT, bold=True)
            pdf.ln(0.5)
            pdf.set_font("Malgun", size=11.5)
            pdf.set_text_color(*MUTED)
            pdf.write(line_h(11.5), f"{art['source']}  |  ")
            pdf.set_text_color(*LINK)
            pdf.write(line_h(11.5), "원문 보기", link=art["url"])
            pdf.ln(line_h(11.5) + 1.5)
            if art.get("summary"):
                text(art["summary"], 13, TEXT)
            pdf.ln(6)

    section("② 인공지능 주요 기사")
    articles(data.get("ai_articles", []))
    section("③ 그 밖의 화제 기사")
    articles(data.get("trending", []))

    out_path.parent.mkdir(exist_ok=True)
    pdf.output(str(out_path))


# ---------------------------------------------------------------- 4. 메일

def send_mail(subject: str, data: dict, pdf_path: Path) -> None:
    sender = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    recipients = [r.strip() for r in os.getenv("MAIL_TO", sender).split(",") if r.strip()]

    lines = ["오늘의 브리핑을 PDF로 첨부합니다.", "", "[인공지능 주요 기사]"]
    lines += [f"- {a['title']}" for a in data.get("ai_articles", [])]
    lines += ["", "[그 밖의 화제 기사]"]
    lines += [f"- {a['title']}" for a in data.get("trending", [])]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content("\n".join(lines))
    msg.add_attachment(pdf_path.read_bytes(), maintype="application",
                       subtype="pdf", filename=pdf_path.name)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
    log.info("메일 발송 완료: %s", ", ".join(recipients))


# ---------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="메일 발송 없이 PDF만 생성")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")
    setup_logging()
    started = time.monotonic()

    now = datetime.now(KST)
    period = "오전" if now.hour < 12 else "오후"
    title = f"AI 브리핑 {now:%Y-%m-%d} ({period})"

    ai, trending = collect_all()
    if not ai and not trending:
        log.error("수집된 기사가 없어 종료합니다.")
        return 1

    try:
        data = summarize(ai, trending)
    except Exception:
        log.exception("요약 실패, 헤드라인 목록으로 대체")
        data = headline_only(ai, trending)

    pdf_path = REPORT_DIR / f"AI브리핑_{now:%Y-%m-%d}_{period}.pdf"
    build_pdf(data, title, pdf_path)
    log.info("PDF 생성: %s", pdf_path)

    if args.dry_run:
        log.info("--dry-run: 메일은 보내지 않습니다.")
    else:
        send_mail(f"[AI 브리핑] {now:%Y-%m-%d} ({period})", data, pdf_path)

    log.info("완료 (%.0f초)", time.monotonic() - started)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log.exception("실행 중 오류")
        sys.exit(1)

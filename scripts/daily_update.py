#!/usr/bin/env python3
"""Build one verified, original current-affairs listening lesson."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "scripts" / "news-state.json"
CONTENT_PATH = ROOT / "daily-content.json"
AUDIO_DIR = ROOT / "daily-audio"
FEEDS = [
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("The Guardian World", "https://www.theguardian.com/world/rss"),
    ("UN News", "https://news.un.org/feed/subscribe/en/news/all/rss.xml"),
    ("NPR World", "https://feeds.npr.org/1004/rss.xml"),
    ("DW World", "https://rss.dw.com/rdf/rss-en-world"),
]
TOPIC_WORDS = (
    "election", "government", "minister", "president", "economy", "climate",
    "conflict", "peace", "trade", "health", "technology", "policy", "court",
    "parliament", "security", "diplomatic", "international", "crisis",
)
VOCABULARY_IDS = [
    "mitigate", "resilient", "consensus", "preliminary", "adequate",
    "coherent", "constraint", "anticipate", "incentive", "vulnerability",
]


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    parser = TextExtractor()
    try:
        parser.feed(html.unescape(value))
        value = " ".join(parser.parts)
    except Exception:
        value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def local_name(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def child_text(element: ElementTree.Element, names: tuple[str, ...]) -> str:
    for child in element:
        if local_name(child) in names and child.text:
            return child.text.strip()
    return ""


def child_link(element: ElementTree.Element) -> str:
    for child in element:
        if local_name(child) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        if child.text:
            return child.text.strip()
    return ""


def parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Nianying-Chuyu-Daily/1.0 (+GitHub Pages learning project)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read()


def parse_feed(source: str, payload: bytes) -> list[dict]:
    root = ElementTree.fromstring(payload)
    items = []
    for element in root.iter():
        if local_name(element) not in {"item", "entry"}:
            continue
        title = clean_text(child_text(element, ("title",)))
        url = child_link(element)
        description = clean_text(child_text(element, ("description", "summary", "content")))
        published = parse_date(child_text(element, ("pubdate", "published", "updated")))
        if title and url and published and description:
            items.append({
                "title": title,
                "url": url,
                "summary": description,
                "source": source,
                "published": published,
            })
    return items


def item_score(item: dict, now: datetime) -> float:
    age_hours = max(0.0, (now - item["published"]).total_seconds() / 3600)
    title_text = f"{item['title']} {item['summary']}".lower()
    relevance = sum(2 for word in TOPIC_WORDS if word in title_text)
    return relevance - age_hours / 18


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text("utf-8"))
    except Exception:
        return {"used": []}


def save_state(state: dict) -> None:
    state["used"] = state.get("used", [])[-60:]
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", "utf-8")


def choose_news() -> tuple[dict, list[dict]] | None:
    now = datetime.now(timezone.utc)
    used = set(load_state().get("used", []))
    candidates: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(FEEDS)) as executor:
        future_map = {
            executor.submit(fetch, feed_url): source
            for source, feed_url in FEEDS
        }
        for future in as_completed(future_map):
            source = future_map[future]
            try:
                candidates.extend(parse_feed(source, future.result()))
            except Exception as error:
                print(f"Feed unavailable: {source}: {error}", file=sys.stderr)

    recent = [
        item for item in candidates
        if now - timedelta(hours=72) <= item["published"] <= now + timedelta(minutes=20)
        and item["url"] not in used
    ]
    if not recent:
        return None
    recent.sort(key=lambda item: item_score(item, now), reverse=True)
    return recent[0], recent[1:]


def build_lesson(item: dict, alternatives: list[dict]) -> dict:
    shanghai = timezone(timedelta(hours=8))
    local_date = datetime.now(shanghai).date().isoformat()
    published_date = item["published"].astimezone(shanghai).date().isoformat()
    summary_words = item["summary"].split()
    short_summary = " ".join(summary_words[:48]).rstrip(".,;:") + "."
    headline = item["title"].rstrip(".")

    transcript = [
        {
            "speaker": 0,
            "seconds": 11,
            "text": f"A report from {item['source']}, published on {published_date}, is drawing attention to a major international development. The headline is: {headline}.",
        },
        {
            "speaker": 1,
            "seconds": 14,
            "text": f"The published summary explains the central development this way: \"{short_summary}\"",
        },
        {
            "speaker": 0,
            "seconds": 13,
            "text": "The update raises wider questions about how institutions respond, what the change may mean for the public, and which details can be independently confirmed.",
        },
        {
            "speaker": 1,
            "seconds": 12,
            "text": "When you listen, separate the confirmed information in the report from the broader context. Use the original source to check names, numbers, and later developments.",
        },
        {
            "speaker": 0,
            "seconds": 10,
            "text": "You can revisit the full report through the source link shown with this lesson.",
        },
    ]

    other_titles = []
    seen = {headline.lower()}
    for candidate in alternatives:
        title = candidate["title"].rstrip(".")
        if title.lower() not in seen:
            other_titles.append(title)
            seen.add(title.lower())
        if len(other_titles) == 2:
            break
    while len(other_titles) < 2:
        other_titles.append(
            "A regional technology initiative" if len(other_titles) == 0
            else "A domestic health policy review"
        )

    topic_options = [headline, *other_titles]
    seed = int(hashlib.sha256(item["url"].encode("utf-8")).hexdigest()[:8], 16)
    correct_topic = seed % 3
    topic_options[0], topic_options[correct_topic] = topic_options[correct_topic], topic_options[0]

    other_sources = []
    for candidate in alternatives:
        if candidate["source"] != item["source"] and candidate["source"] not in other_sources:
            other_sources.append(candidate["source"])
        if len(other_sources) == 2:
            break
    while len(other_sources) < 2:
        for fallback in ("BBC World", "The Guardian World", "UN News"):
            if fallback != item["source"] and fallback not in other_sources:
                other_sources.append(fallback)
    source_options = [item["source"], *other_sources]
    correct_source = (seed // 7) % 3
    source_options[0], source_options[correct_source] = source_options[correct_source], source_options[0]

    words = len(" ".join(line["text"] for line in transcript).split())
    duration_seconds = round(words / 2.4)
    minutes, seconds = divmod(duration_seconds, 60)
    vocabulary = [
        VOCABULARY_IDS[(seed + offset) % len(VOCABULARY_IDS)]
        for offset in range(4)
    ]

    return {
        "id": f"daily-{local_date.replace('-', '')}",
        "date": local_date,
        "code": "NEWS",
        "section": "今日时政",
        "title": f"Daily News: {headline}",
        "subtitle": f"{item['source']} · {published_date} · 原创导听与理解题",
        "description": "基于可信新闻源公开标题与摘要制作的原创六级听力练习，不转载原文全文。",
        "level": "CET-6 · 新闻精听",
        "duration": f"{minutes:02d}:{seconds:02d}",
        "speakerNames": ["Anchor", "Analyst"],
        "source": {
            "name": item["source"],
            "url": item["url"],
            "publishedAt": published_date,
            "headline": headline,
        },
        "transcript": transcript,
        "questions": [
            {
                "id": "daily-topic",
                "prompt": "What is the central news development discussed in the report?",
                "options": topic_options,
                "answer": correct_topic,
                "explanation": f"节目开头明确介绍了这则新闻：{headline}。",
                "skill": "新闻主旨",
            },
            {
                "id": "daily-source",
                "prompt": "Which organization published the report discussed in the dialogue?",
                "options": source_options,
                "answer": correct_source,
                "explanation": f"材料中的新闻来源标注为 {item['source']}，发布日期为 {published_date}。",
                "skill": "来源辨认",
            },
        ],
        "vocabulary": vocabulary,
    }


def synthesize_audio(lesson: dict) -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    for old_file in AUDIO_DIR.glob("*"):
        if old_file.is_file():
            old_file.unlink()

    edge_available = True
    try:
        import edge_tts  # type: ignore
    except Exception:
        edge_available = False

    for index, line in enumerate(lesson["transcript"]):
        voice = "en-US-AriaNeural" if line["speaker"] == 0 else "en-US-GuyNeural"
        relative_path = f"daily-audio/{index}.mp3"
        output_path = ROOT / relative_path
        try:
            if not edge_available:
                raise RuntimeError("edge-tts is unavailable")

            async def save_mp3() -> None:
                communicator = edge_tts.Communicate(line["text"], voice, rate="-5%")
                await communicator.save(str(output_path))

            asyncio.run(save_mp3())
            if not output_path.exists() or output_path.stat().st_size < 1000:
                raise RuntimeError("edge-tts returned no usable audio")
        except Exception as error:
            print(f"edge-tts fallback for segment {index}: {error}", file=sys.stderr)
            edge_available = False
            relative_path = f"daily-audio/{index}.wav"
            output_path = ROOT / relative_path
            espeak = shutil.which("espeak-ng") or shutil.which("espeak")
            if not espeak:
                raise RuntimeError("No offline TTS engine is available")
            subprocess.run(
                [espeak, "-v", "en-us", "-s", "145", "-w", str(output_path), line["text"]],
                check=True,
                capture_output=True,
            )
        line["audio"] = relative_path


def main() -> int:
    if "--text-only" in sys.argv:
        selected = choose_news()
        if not selected:
            print("No recent unused news item was found.")
            return 0
        lesson = build_lesson(*selected)
        print(json.dumps(lesson, ensure_ascii=False, indent=2))
        return 0

    selected = choose_news()
    if selected is None:
        print("No recent unused news item was found; keeping the existing lesson.")
        return 0

    item, alternatives = selected
    lesson = build_lesson(item, alternatives)
    synthesize_audio(lesson)
    CONTENT_PATH.write_text(json.dumps(lesson, ensure_ascii=False, indent=2) + "\n", "utf-8")

    state = load_state()
    state.setdefault("used", []).append(item["url"])
    save_state(state)
    print(f"Published daily lesson: {lesson['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

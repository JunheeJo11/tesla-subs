"""
make_subs.py

Bad Friends, TigerBelly 팟캐스트 에피소드의 자막을 집 PC GPU(Whisper)로 미리 만들어 둔다.
만든 자막은 subs/<쇼 이름>/ 폴더에 에피소드마다 JSON 파일 하나로 저장되고,
subs/<쇼 이름>/index.json 에 에피소드 목록이 정리된다.

사용 예
  bash run.sh --limit 1              # 테스트: 쇼마다 가장 오래된 1편만
  bash run.sh                        # 전체, 1화부터 순서대로 (이미 만든 에피소드는 건너뜀)
  bash run.sh --newest               # 최신 에피소드부터
  bash run.sh --show badfriends      # 한 쇼만
  bash run.sh --model large-v3       # 더 느리지만 조금 더 정확한 모델

중간에 Ctrl+C로 멈춰도, 다시 실행하면 끝난 에피소드는 건너뛰고 이어서 진행한다.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

import feedparser
import requests

FEEDS = {
    "badfriends": "https://feeds.megaphone.fm/TPC1602991613",
    "tigerbelly": "https://rss.art19.com/tigerbelly-podcast",
}

OUT_DIR = "subs"
TMP_DIR = "tmp_audio"
FAILED_LOG = "failed.txt"
USER_AGENT = "Mozilla/5.0 (personal podcast subtitle tool)"


# ---------------------------------------------------------------- 피드 정보 정리

def episode_id(entry):
    """에피소드마다 바뀌지 않는 짧은 ID. 파일 이름으로 쓴다."""
    raw = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title", "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def published_iso(entry):
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return None
    return datetime(*t[:6], tzinfo=timezone.utc).isoformat()


def feed_duration_sec(entry):
    """RSS에 적힌 길이(itunes:duration). 'HH:MM:SS', 'MM:SS', 초 숫자 모두 처리."""
    raw = str(entry.get("itunes_duration", "") or "").strip()
    if not raw:
        return None
    try:
        parts = [float(p) for p in raw.split(":")]
    except ValueError:
        return None
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return round(sec)


def audio_url(entry):
    for enc in entry.get("enclosures", []):
        href = enc.get("href") or enc.get("url")
        if href:
            return href
    for link in entry.get("links", []):
        if link.get("rel") == "enclosure" and link.get("href"):
            return link["href"]
    return None


# ---------------------------------------------------------------- 파일 처리

def write_json_atomic(path, data):
    """쓰는 도중 꺼져도 깨진 파일이 남지 않게, 임시 파일에 쓴 뒤 이름을 바꾼다."""
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def download(url, path, retries=3):
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=60,
                              headers={"User-Agent": USER_AGENT}) as r:
                r.raise_for_status()
                tmp = path + ".part"
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                os.replace(tmp, path)
            return
        except Exception as err:
            if attempt == retries:
                raise
            print(f"    다운로드 재시도 {attempt}/{retries - 1}: {err}")
            time.sleep(5 * attempt)


def log_failed(show, title, err):
    with open(FAILED_LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')}\t{show}\t{title}\t{err}\n")


def write_index(show, entries_info):
    """쇼의 에피소드 목록. 최신 에피소드가 맨 앞."""
    show_dir = os.path.join(OUT_DIR, show)
    items = []
    for info in entries_info:
        has_subs = os.path.exists(os.path.join(show_dir, info["id"] + ".json"))
        items.append({**info, "has_subs": has_subs})
    items.sort(key=lambda x: x["published"] or "", reverse=True)
    write_json_atomic(os.path.join(show_dir, "index.json"), {
        "show": show,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "episodes": items,
    })


# ---------------------------------------------------------------- 자막 생성

def load_model(name):
    from faster_whisper import WhisperModel
    print(f"Whisper 모델 불러오는 중: {name} (처음 한 번은 다운로드에 몇 분 걸림)")
    try:
        return WhisperModel(name, device="cuda", compute_type="float16")
    except Exception as err:
        print("\nGPU로 모델을 불러오지 못했습니다. 아래 오류 메시지를 그대로 알려주세요.")
        print(f"  {type(err).__name__}: {err}")
        sys.exit(1)


def transcribe(model, path):
    segments, info = model.transcribe(
        path,
        language="en",
        vad_filter=True,                    # 무음 구간을 건너뛰어 속도와 정확도 개선
        beam_size=5,
        condition_on_previous_text=False,   # 긴 오디오에서 같은 문장이 반복되는 현상 방지
    )
    segs = []
    for s in segments:
        text = s.text.strip()
        if text:
            segs.append({"s": round(s.start, 2), "e": round(s.end, 2), "t": text})
    return segs, info.duration


def process_show(model, model_name, show, url, limit, newest_first):
    show_dir = os.path.join(OUT_DIR, show)
    os.makedirs(show_dir, exist_ok=True)

    print(f"\n=== {show}: 피드 읽는 중")
    feed = feedparser.parse(url, agent=USER_AGENT)
    entries = feed.entries
    if not entries:
        print(f"  에피소드를 하나도 못 읽었습니다. 피드 주소나 인터넷 연결을 확인하세요: {url}")
        return

    entries_info = []
    for e in entries:
        entries_info.append({
            "id": episode_id(e),
            "title": e.get("title", "").strip(),
            "published": published_iso(e),
            "feed_duration": feed_duration_sec(e),
        })

    # 기본은 가장 오래된 에피소드(1화)부터, --newest를 주면 최신부터 처리
    order = sorted(zip(entries, entries_info),
                   key=lambda x: x[1]["published"] or "", reverse=newest_first)
    if limit:
        order = order[:limit]

    todo = [(e, i) for e, i in order
            if not os.path.exists(os.path.join(show_dir, i["id"] + ".json"))]
    print(f"  전체 {len(entries)}편, 이번 대상 {len(order)}편, 새로 만들 것 {len(todo)}편")
    write_index(show, entries_info)

    for n, (e, info) in enumerate(todo, 1):
        title = info["title"]
        out_path = os.path.join(show_dir, info["id"] + ".json")
        audio_path = os.path.join(TMP_DIR, info["id"] + ".mp3")
        print(f"\n[{n}/{len(todo)}] {title}")
        try:
            url_audio = audio_url(e)
            if not url_audio:
                raise RuntimeError("RSS에 음성 파일 주소가 없음")

            t0 = time.time()
            download(url_audio, audio_path)
            t1 = time.time()
            segs, dur = transcribe(model, audio_path)
            t2 = time.time()

            write_json_atomic(out_path, {
                "show": show,
                "id": info["id"],
                "title": title,
                "published": info["published"],
                "audio_duration": round(dur, 2),     # 내가 받은 파일의 실제 길이
                "feed_duration": info["feed_duration"],
                "model": model_name,
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "segments": segs,
            })
            speed = dur / max(t2 - t1, 0.1)
            print(f"    완료: 자막 {len(segs)}줄, 길이 {dur / 60:.0f}분, "
                  f"다운로드 {t1 - t0:.0f}초, 변환 {t2 - t1:.0f}초 (실시간의 {speed:.0f}배 속도)")
            write_index(show, entries_info)
        except KeyboardInterrupt:
            raise
        except Exception as err:
            print(f"    실패: {err}")
            log_failed(show, title, err)
        finally:
            for p in (audio_path, audio_path + ".part"):
                if os.path.exists(p):
                    os.remove(p)


def main():
    ap = argparse.ArgumentParser(description="팟캐스트 자막 일괄 생성")
    ap.add_argument("--show", choices=sorted(FEEDS), help="이 쇼만 처리")
    ap.add_argument("--limit", type=int, default=0, help="쇼마다 N편만 처리 (테스트용)")
    ap.add_argument("--newest", action="store_true",
                    help="최신 에피소드부터 처리 (기본은 가장 오래된 1화부터)")
    ap.add_argument("--model", default="large-v3-turbo",
                    help="Whisper 모델 (기본: large-v3-turbo, 더 정확하게: large-v3)")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)

    model = load_model(args.model)
    shows = [args.show] if args.show else list(FEEDS)
    try:
        for show in shows:
            process_show(model, args.model, show, FEEDS[show], args.limit, args.newest)
    except KeyboardInterrupt:
        print("\n중단했습니다. 다시 실행하면 끝난 에피소드는 건너뛰고 이어서 진행합니다.")
        sys.exit(130)
    print("\n모두 끝났습니다. 실패한 에피소드가 있으면 failed.txt를 확인하세요.")


if __name__ == "__main__":
    main()

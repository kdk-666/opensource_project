"""카드 이미지와 나레이션 음성을 합쳐 세로 영상(mp4)을 만든다. FFmpeg를 사용한다.

- 카드마다 '이미지 + 음성' 한 조각(segment)을 만들고, 조각들을 이어 붙인다. (조각은 같은 설정이라 다시 인코딩하지 않는다)
- 카드 표시 시간 = 앞 여유 + 실제 음성 길이 + 뒤 여유. (대본의 추정이 아니라 data/audio.json의 실제 길이)
- 음성이 없는 카드(음성 생성 실패)는 무음으로 대본 추정 시간만큼 보여 주고 알린다.
- 1080x1920, 30fps, H.264 + AAC, yuv420p (대부분의 플레이어/플랫폼에서 재생되는 설정)
"""
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

LEAD_SEC = 0.35  # 카드가 나온 뒤 음성이 시작되기까지
TAIL_SEC = 0.65  # 음성이 끝난 뒤 다음 카드로 넘어가기까지
MIN_SILENT_SEC = 3.0  # 음성이 없는 카드의 최소 표시 시간
FPS = 30
SAMPLE_RATE = 44100


class FFmpegMissing(RuntimeError):
    pass


@dataclass
class Segment:
    index: int
    kind: str
    image: str
    audio: str  # 비어 있으면 무음
    duration: float  # 이 카드가 화면에 나오는 시간(초)
    note: str = ""


def plan_segments(cards: list[dict], audio: list[dict]) -> list[Segment]:
    """cards.json(한 테마)과 audio.json을 카드 번호로 맞춰 조각 계획을 만든다."""
    by_index = {a["index"]: a for a in audio}
    plan = []
    for c in sorted(cards, key=lambda x: x["index"]):
        a = by_index.get(c["index"])
        if a and a.get("status") == "ok" and a.get("duration_sec", 0) > 0:
            plan.append(Segment(c["index"], c["kind"], c["image"], a["audio"],
                                round(LEAD_SEC + a["duration_sec"] + TAIL_SEC, 2)))
        else:
            reason = "음성 생성에 실패" if a else "음성 목록에 없음"
            dur = max(MIN_SILENT_SEC, float(c.get("est_sec") or 0) + LEAD_SEC + TAIL_SEC)
            plan.append(Segment(c["index"], c["kind"], c["image"], "", round(dur, 2),
                                note=f"{reason}해서 무음으로 {dur:.1f}초 표시"))
    return plan


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg 실행 실패:\n{' '.join(cmd[:6])} ...\n{proc.stderr[-600:]}")


def check_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise FFmpegMissing(f"{tool}을(를) 찾지 못했어요. Docker에서는 이미지에 포함돼 있고, "
                                f"내 PC에서 직접 실행한다면 FFmpeg를 설치해야 해요.")


def build_segment(seg: Segment, out: Path) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-i", seg.image]
    if seg.audio:
        cmd += ["-i", seg.audio, "-af", f"adelay={int(LEAD_SEC * 1000)}:all=1,apad"]
    else:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo"]
    cmd += ["-t", f"{seg.duration:.2f}", "-r", str(FPS),
            "-vf", "scale=1080:1920:flags=lanczos,format=yuv420p",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-tune", "stillimage",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE), "-ac", "2",
            "-movflags", "+faststart", str(out)]
    _run(cmd)


def concat_segments(parts: list[Path], out: Path) -> None:
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    try:
        _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
              "-c", "copy", "-movflags", "+faststart", str(out)])
    finally:
        listing.unlink(missing_ok=True)


def probe(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration,size:stream=codec_name,width,height",
         "-of", "json", str(path)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 실패: {proc.stderr[-300:]}")
    data = json.loads(proc.stdout)
    video = next((s for s in data["streams"] if s.get("codec_name") == "h264"), {})
    return {"duration": round(float(data["format"]["duration"]), 2), "size": int(data["format"]["size"]),
            "width": video.get("width"), "height": video.get("height"),
            "codecs": [s.get("codec_name") for s in data["streams"]]}


def make_video(plan: list[Segment], out: Path, keep_tmp: bool = False) -> dict:
    """조각 계획으로 영상 하나를 만든다. 중간에 실패하면 반쪽짜리 파일을 남기지 않는다."""
    check_tools()
    if not plan:
        raise ValueError("만들 카드가 없어요.")
    for s in plan:
        if not Path(s.image).exists():
            raise FileNotFoundError(f"카드 이미지가 없어요: {s.image} (먼저 `newscard cards`를 실행하세요)")
        if s.audio and not Path(s.audio).exists():
            raise FileNotFoundError(f"음성 파일이 없어요: {s.audio} (먼저 `newscard tts`를 실행하세요)")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / f"_tmp_{out.stem}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        parts = []
        for s in plan:
            part = tmp / f"seg_{s.index:02d}.mp4"
            build_segment(s, part)
            parts.append(part)
        concat_segments(parts, out)
    except Exception:
        out.unlink(missing_ok=True)
        raise
    finally:
        if not keep_tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    info = probe(out)
    info["expected"] = round(sum(s.duration for s in plan), 2)
    return info


def run(data_dir: str = "data", themes: tuple[str, ...] = ("light", "dark")) -> dict[str, dict]:
    d = Path(data_dir)
    for name, hint in (("cards.json", "newscard cards"), ("audio.json", "newscard tts")):
        if not (d / name).exists():
            raise SystemExit(f"data/{name} 이 없어요. 먼저 `{hint}`를 실행하세요.")
    cards = json.loads((d / "cards.json").read_text(encoding="utf-8"))
    audio = json.loads((d / "audio.json").read_text(encoding="utf-8"))
    results = {}
    for theme in themes:
        if theme not in cards:
            raise SystemExit(f"{theme} 카드가 없어요. `newscard cards {theme}`를 먼저 실행하세요.")
        plan = plan_segments(cards[theme], audio)
        try:
            info = make_video(plan, d / "video" / f"newscard_{theme}.mp4")
        except (FFmpegMissing, FileNotFoundError, ValueError) as e:
            raise SystemExit(str(e))
        info.update(path=str(d / "video" / f"newscard_{theme}.mp4").replace("\\", "/"),
                    notes=[s.note for s in plan if s.note],
                    cards=[{"index": s.index, "kind": s.kind, "seconds": s.duration} for s in plan])
        results[theme] = info
    return results

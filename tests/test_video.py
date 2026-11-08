"""영상 단계 테스트. 실제 FFmpeg로 짧은 영상을 만들어 확인한다. FFmpeg가 없으면 해당 테스트는 건너뛴다."""
import json
import shutil
import wave

import pytest
from PIL import Image

from newscard import video as V

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg 없음")


def png(path, color=(20, 30, 60)):
    Image.new("RGB", (1080, 1920), color).save(path)
    return str(path).replace("\\", "/")


def wav(path, seconds, rate=22050):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return str(path).replace("\\", "/")


def card(i, img, kind="news", est=5.0):
    return {"index": i, "kind": kind, "rank": i, "image": img, "est_sec": est}


def audio(i, path, sec, status="ok"):
    return {"index": i, "kind": "news", "audio": path, "duration_sec": sec, "status": status, "est_sec": 5.0}


# ---- 계획 (FFmpeg 없이) ----
def test_plan_uses_real_audio_length_plus_padding():
    plan = V.plan_segments([card(0, "a.png"), card(1, "b.png")], [audio(0, "a.wav", 4.0), audio(1, "b.wav", 6.5)])
    assert [s.duration for s in plan] == [round(V.LEAD_SEC + 4.0 + V.TAIL_SEC, 2), round(V.LEAD_SEC + 6.5 + V.TAIL_SEC, 2)]
    assert all(s.audio and not s.note for s in plan)


def test_plan_orders_by_index_and_handles_missing_audio():
    plan = V.plan_segments([card(2, "c.png", est=1.0), card(0, "a.png"), card(1, "b.png", est=8.0)],
                           [audio(0, "a.wav", 3.0), audio(1, "b.wav", 0, status="failed")])
    assert [s.index for s in plan] == [0, 1, 2]
    failed, absent = plan[1], plan[2]
    assert failed.audio == "" and failed.duration == round(8.0 + V.LEAD_SEC + V.TAIL_SEC, 2) and "실패" in failed.note
    assert absent.audio == "" and absent.duration == V.MIN_SILENT_SEC and "목록에 없음" in absent.note  # 최소 표시 시간


# ---- 실제 영상 ----
@needs_ffmpeg
def test_make_video_end_to_end(tmp_path):
    imgs = [png(tmp_path / f"c{i}.png", (20 * i, 30, 60)) for i in range(3)]
    cards = [card(0, imgs[0], "intro"), card(1, imgs[1]), card(2, imgs[2], "outro", est=2.0)]
    audios = [audio(0, wav(tmp_path / "a0.wav", 1.0), 1.0), audio(1, wav(tmp_path / "a1.wav", 2.0), 2.0),
              audio(2, "", 0, status="failed")]
    plan = V.plan_segments(cards, audios)
    out = tmp_path / "video" / "out.mp4"
    info = V.make_video(plan, out)

    assert out.exists() and (info["width"], info["height"]) == (1080, 1920)
    assert "h264" in info["codecs"] and "aac" in info["codecs"]
    assert abs(info["duration"] - info["expected"]) < 0.4  # 계획한 길이와 실제 길이가 거의 같다
    assert not any(p.name.startswith("_tmp_") for p in out.parent.iterdir())  # 중간 파일은 지워진다
    assert not list(out.parent.glob("*.txt"))


@needs_ffmpeg
def test_video_has_audible_audio_track_with_requested_lead_silence(tmp_path):
    img = png(tmp_path / "c.png")
    plan = [V.Segment(0, "news", img, "", 1.5)]
    info = V.make_video(plan, tmp_path / "o.mp4")
    assert "aac" in info["codecs"]  # 무음이어도 오디오 트랙은 있다(플랫폼 호환)


@needs_ffmpeg
def test_missing_files_give_clear_errors(tmp_path):
    img = png(tmp_path / "c.png")
    with pytest.raises(FileNotFoundError, match="newscard cards"):
        V.make_video([V.Segment(0, "news", str(tmp_path / "없음.png"), "", 2.0)], tmp_path / "o.mp4")
    with pytest.raises(FileNotFoundError, match="newscard tts"):
        V.make_video([V.Segment(0, "news", img, str(tmp_path / "없음.wav"), 2.0)], tmp_path / "o.mp4")
    with pytest.raises(ValueError):
        V.make_video([], tmp_path / "o.mp4")


@needs_ffmpeg
def test_failure_leaves_no_half_made_video(tmp_path, monkeypatch):
    img = png(tmp_path / "c.png")
    calls = {"n": 0}
    real = V.build_segment

    def flaky(seg, out):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("인코딩 실패")
        real(seg, out)

    monkeypatch.setattr(V, "build_segment", flaky)
    out = tmp_path / "video" / "o.mp4"
    with pytest.raises(RuntimeError, match="인코딩 실패"):
        V.make_video([V.Segment(0, "news", img, "", 1.0), V.Segment(1, "news", img, "", 1.0)], out)
    assert not out.exists() and not any(p.name.startswith("_tmp_") for p in out.parent.iterdir())


def test_ffmpeg_missing_message(monkeypatch, tmp_path):
    monkeypatch.setattr(V.shutil, "which", lambda name: None)
    with pytest.raises(V.FFmpegMissing, match="FFmpeg"):
        V.make_video([V.Segment(0, "news", "a.png", "", 1.0)], tmp_path / "o.mp4")


# ---- run ----
def test_run_requires_previous_steps(tmp_path):
    with pytest.raises(SystemExit, match="newscard cards"):
        V.run(str(tmp_path))
    (tmp_path / "cards.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="newscard tts"):
        V.run(str(tmp_path))
    (tmp_path / "audio.json").write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit, match="dark"):
        V.run(str(tmp_path), themes=("dark",))


@needs_ffmpeg
def test_run_makes_one_video_per_theme(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    manifest = {t: [card(0, png(d / f"{t}_0.png", (10, 10, 10)), "intro"), card(1, png(d / f"{t}_1.png", (200, 200, 200)))]
                for t in ("light", "dark")}
    (d / "cards.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "audio.json").write_text(json.dumps([audio(0, wav(d / "a0.wav", 1.0), 1.0), audio(1, wav(d / "a1.wav", 1.5), 1.5)]),
                                  encoding="utf-8")
    res = V.run(str(d))
    assert set(res) == {"light", "dark"} and (d / "video" / "newscard_light.mp4").exists() and (d / "video" / "newscard_dark.mp4").exists()
    assert res["dark"]["cards"][1]["seconds"] == round(V.LEAD_SEC + 1.5 + V.TAIL_SEC, 2)

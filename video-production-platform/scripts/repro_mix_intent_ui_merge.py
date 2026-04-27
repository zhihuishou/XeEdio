#!/usr/bin/env python3
"""Minimal repro: when NL intent parsing fails, UI/request fields must win.

Bug (fixed): ParsedIntent.defaults() was merged on top of UI, so video_count
became 1 and max_output_duration 60 even when the panel sent 2 / 18.

Usage (cwd = video-production-platform/):

  python3 scripts/repro_mix_intent_ui_merge.py
      → merge-only check (no DB, no server)

  python3 scripts/repro_mix_intent_ui_merge.py --db-smoke
      → creates one Task with LLM parse forced to fail; asserts mix_contract
        matches request; does not run the mix pipeline (background thread mocked)

Requires: PYTHONPATH or run from video-production-platform so ``app`` imports.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_merge_only() -> None:
    from app.services.intent_parsing_service import IntentParsingService, ParsedIntent

    parsed = ParsedIntent.defaults()
    ui = {
        "video_count": 2,
        "max_output_duration": 18,
        "aspect_ratio": "16:9",
        "tts_text": None,
        "bgm_enabled": False,
    }
    merged_fail = IntentParsingService.merge_with_ui_defaults(
        parsed, ui, llm_parse_succeeded=False
    )
    merged_ok = IntentParsingService.merge_with_ui_defaults(
        parsed, ui, llm_parse_succeeded=True
    )

    print("[merge-only] llm_parse_succeeded=False (simulated NL failure)")
    print(f"  video_count={merged_fail['video_count']} max_output_duration={merged_fail['max_output_duration']}")
    assert merged_fail["video_count"] == 2, merged_fail
    assert merged_fail["max_output_duration"] == 18, merged_fail
    assert merged_fail["aspect_ratio"] == "16:9", merged_fail

    print("[merge-only] llm_parse_succeeded=True (parsed defaults still override UI)")
    print(f"  video_count={merged_ok['video_count']} max_output_duration={merged_ok['max_output_duration']}")
    assert merged_ok["video_count"] == 1
    assert merged_ok["max_output_duration"] == 60

    print("merge-only: OK")


def run_db_smoke() -> None:
    from app.models.database import Asset, Base, SessionLocal, Task, User, engine, generate_uuid
    from app.schemas.mix import MixCreateRequest
    from app.services.intent_parsing_service import ParsedIntent
    from app.services.mixing_service import MixingService

    fd, dummy_path = tempfile.mkstemp(suffix=".mp4", prefix="repro_mix_")
    os.close(fd)

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    user_id = generate_uuid()
    asset_id = generate_uuid()
    task_id_holder: list[str | None] = [None]

    try:
        db.add(
            User(
                id=user_id,
                username=f"repro_merge_{user_id[:8]}",
                password_hash="unused",
                role="admin",
            )
        )
        db.add(
            Asset(
                id=asset_id,
                filename="repro.mp4",
                original_filename="repro.mp4",
                file_path=dummy_path,
                thumbnail_path=None,
                category="product",
                media_type="video",
                file_format="mp4",
                file_size=1,
                duration=30.0,
                uploaded_by=user_id,
            )
        )
        db.commit()

        req = MixCreateRequest(
            topic="repro intent ui wins",
            asset_ids=[asset_id],
            video_count=2,
            max_output_duration=18,
            aspect_ratio="16:9",
            director_prompt="这条指令会走 parse_intent_with_meta；此处被 mock 为失败",
        )

        def fake_parse_meta(_self, _prompt: str):
            return ParsedIntent.defaults(), False

        mock_thread = MagicMock()

        with patch(
            "app.services.mixing_service.IntentParsingService.parse_intent_with_meta",
            new=fake_parse_meta,
        ):
            with patch("app.services.mixing_service.threading.Thread", return_value=mock_thread):
                svc = MixingService(db)
                task = svc.create_mix_task(req, user_id)
                task_id_holder[0] = task.id

        mock_thread.start.assert_called_once()
        db.expire_all()
        task2 = db.query(Task).filter(Task.id == task.id).first()
        assert task2 is not None
        params = json.loads(task2.mix_params)
        contract = params.get("mix_contract") or {}

        print("[db-smoke] mix_params after create (LLM forced fail):")
        print(f"  intent_parse_ok={params.get('intent_parse_ok')}")
        print(f"  mix_contract.video_count={contract.get('video_count')}")
        print(f"  mix_contract.max_output_duration={contract.get('max_output_duration')}")
        print(f"  mix_contract.aspect_ratio={contract.get('aspect_ratio')}")

        assert params.get("intent_parse_ok") is False
        assert contract.get("video_count") == 2, contract
        assert contract.get("max_output_duration") == 18, contract
        assert contract.get("aspect_ratio") == "16:9", contract

        print("db-smoke: OK (task left in DB; delete manually if desired:", task.id, ")")

    finally:
        db.close()
        try:
            os.unlink(dummy_path)
        except OSError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-smoke",
        action="store_true",
        help="Create one task in sqlite with mocked failed NL parse (no pipeline run).",
    )
    args = parser.parse_args()
    run_merge_only()
    if args.db_smoke:
        os.chdir(ROOT)
        run_db_smoke()


if __name__ == "__main__":
    main()

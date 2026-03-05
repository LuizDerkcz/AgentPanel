"""
清空所有 source_lang='en' 的 thread.summary 和 debate_summary，
让 summary_job 下次运行时重新用英文生成。

用法: cd backend && python app/scripts/clear_en_summaries.py
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.session import engine
from app.models.forum import Thread


def main() -> None:
    with Session(engine) as db:
        result = db.execute(
            update(Thread)
            .where(Thread.source_lang == "en")
            .values(summary=None, debate_summary=None)
        )
        db.commit()
        print(f"已清空 {result.rowcount} 条英文 thread 的 summary，等待 summary_job 重新生成。")


if __name__ == "__main__":
    main()

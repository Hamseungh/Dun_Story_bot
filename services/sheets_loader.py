import os
import sqlite3
from pathlib import Path
from datetime import datetime

import gspread
from dotenv import load_dotenv

from database import DB_PATH

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
CREDENTIALS_PATH = BASE_DIR / "credentials.json"


def get_spreadsheet():
    if not SHEET_ID:
        raise ValueError("GOOGLE_SHEET_ID가 설정되지 않았습니다.")

    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError("credentials.json 파일이 없습니다.")

    client = gspread.service_account(
        filename=str(CREDENTIALS_PATH)
    )
    return client.open_by_key(SHEET_ID)


def read_sheet(spreadsheet, sheet_name, required_columns):
    worksheet = spreadsheet.worksheet(sheet_name)
    headers = worksheet.row_values(1)

    missing = set(required_columns) - set(headers)
    if missing:
        raise ValueError(
            f"{sheet_name} 시트에 필수 컬럼이 없습니다: "
            f"{sorted(missing)}"
        )

    return worksheet.get_all_records(
        expected_headers=headers
    )


def validate_stories(rows):
    stories = []
    ids = set()

    for row in rows:
        story_id = str(row["story_id"]).strip()
        if not story_id:
            raise ValueError("story_id가 비어 있습니다.")

        if story_id in ids:
            raise ValueError(f"중복 story_id: {story_id}")
        ids.add(story_id)

        title = str(row["title"]).strip()
        start_scene = str(row["start_scene_id"]).strip()

        if not title or not start_scene:
            raise ValueError(
                f"{story_id}: 제목 또는 시작 장면이 없습니다."
            )

        version = int(row["version"])
        enabled = int(row["enabled"])

        if version < 1 or enabled not in (0, 1):
            raise ValueError(f"{story_id}: 버전 또는 enabled 오류")

        availability = str(row.get('availability', '') or 'weekly').strip().lower()
        if availability not in ('weekly', 'permanent'):
            raise ValueError(f'{story_id}: availability는 weekly 또는 permanent여야 합니다.')
        limit_text = str(row.get('clear_limit', '') or '').strip()
        if limit_text:
            try:
                clear_limit = int(limit_text)
            except ValueError:
                raise ValueError(f'{story_id}: clear_limit는 양의 정수 또는 빈칸이어야 합니다.')
            if clear_limit < 1:
                raise ValueError(f'{story_id}: clear_limit는 1 이상이어야 합니다.')
        else:
            clear_limit = None
        stories.append((
            story_id,
            title,
            str(row["description"]),
            str(row["genre"]),
            start_scene,
            version,
            enabled, availability, clear_limit
        ))

    return stories


def validate_schedule(rows, stories):
    """주간 편성은 주당 0~2개, 표시 순서는 1부터 연속으로 지정한다."""
    schedules = []
    seen = set()
    week_orders = {}
    story_status = {story[0]: (story[6], story[7]) for story in stories}

    for row in rows:
        week = str(row["week_start"]).strip()
        story_id = str(row["story_id"]).strip()
        try:
            order = int(str(row["display_order"]).strip())
        except (ValueError, TypeError):
            raise ValueError(f"{week}: display_order는 1 또는 2여야 합니다.")

        parsed = datetime.strptime(week, "%Y-%m-%d").date()
        if parsed.weekday() != 0:
            raise ValueError(f"{week}: 월요일 날짜가 아닙니다.")

        status = story_status.get(story_id)
        if status is None:
            raise ValueError(f"{story_id}: Stories에 등록되지 않은 스토리입니다.")
        enabled, availability = status
        if enabled != 1:
            raise ValueError(f"{story_id}: 비활성 스토리는 주간 편성에 등록할 수 없습니다.")
        if availability != 'weekly':
            raise ValueError(f"{story_id}: 상시(permanent) 스토리는 주간 편성에 등록하지 않습니다.")

        key = (week, story_id)
        if key in seen:
            raise ValueError(f"중복 주간 편성: {key}")
        seen.add(key)

        orders = week_orders.setdefault(week, set())
        if order in orders:
            raise ValueError(f"{week}: 표시 순서 중복")
        orders.add(order)
        schedules.append((week, story_id, order))

    for week, orders in week_orders.items():
        if orders not in ({1}, {1, 2}):
            raise ValueError(f"{week}: 주간 스토리는 1~2개를 순서 1부터 지정하세요.")

    return schedules


def sync_sheets():
    spreadsheet = get_spreadsheet()

    story_rows = read_sheet(
        spreadsheet,
        "Stories",
        [
            "story_id", "title", "description",
            "genre", "start_scene_id",
            "version", "enabled"
        ]
    )

    schedule_rows = read_sheet(
        spreadsheet,
        "WeeklySchedule",
        ["week_start", "story_id", "display_order"]
    )

    stories = validate_stories(story_rows)
    schedules = validate_schedule(schedule_rows, stories)

    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        # 기존 스토리 ID를 유지하며 정보 갱신
        conn.executemany("""
            INSERT INTO stories (
                story_id, title, description, genre,
                start_scene_id, version, enabled, availability, clear_limit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(story_id) DO UPDATE SET
                title = excluded.title,
                description = excluded.description,
                genre = excluded.genre,
                start_scene_id = excluded.start_scene_id,
                version = excluded.version,
                enabled = excluded.enabled,
                availability = excluded.availability,
                clear_limit = excluded.clear_limit
        """, stories)

        # 편성표를 새 내용으로 교체
        conn.execute("DELETE FROM weekly_stories")

        conn.executemany("""
            INSERT INTO weekly_stories (
                week_start, story_id, display_order
            ) VALUES (?, ?, ?)
        """, schedules)

    print(f"스토리 {len(stories)}개 동기화 완료")
    print(f"주간 편성 {len(schedules)}개 동기화 완료")


if __name__ == "__main__":
    sync_sheets()

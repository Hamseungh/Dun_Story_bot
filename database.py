"""Single source of truth for the fresh SQLite schema (no legacy migration)."""
import sqlite3
from pathlib import Path
DB_PATH = Path(__file__).resolve().parent / 'Dun_story.db'

def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript("""
        CREATE TABLE IF NOT EXISTS stories (
            story_id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT DEFAULT '',
            genre TEXT DEFAULT '', start_scene_id TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
            availability TEXT NOT NULL DEFAULT 'weekly', clear_limit INTEGER
        );
        CREATE TABLE IF NOT EXISTS characters (
            character_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sheet_character_id TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL, name TEXT NOT NULL COLLATE NOCASE,
            grade TEXT NOT NULL, job TEXT NOT NULL,
            hp_stat INTEGER NOT NULL DEFAULT 0, mental_stat INTEGER NOT NULL DEFAULT 0,
            agility INTEGER NOT NULL DEFAULT 0, intelligence INTEGER NOT NULL DEFAULT 0,
            strength INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id,name)
        );
        CREATE TABLE IF NOT EXISTS character_progress (
            character_id INTEGER PRIMARY KEY REFERENCES characters(character_id) ON DELETE CASCADE,
            level INTEGER NOT NULL DEFAULT 1 CHECK(level>=1),
            exp INTEGER NOT NULL DEFAULT 0 CHECK(exp>=0),
            gold INTEGER NOT NULL DEFAULT 0 CHECK(gold>=0),
            current_hp INTEGER NOT NULL DEFAULT 0 CHECK(current_hp>=0),
            current_mental INTEGER NOT NULL DEFAULT 0 CHECK(current_mental>=0),
            bonus_hp INTEGER NOT NULL DEFAULT 0, bonus_mental INTEGER NOT NULL DEFAULT 0,
            bonus_agility INTEGER NOT NULL DEFAULT 0, bonus_intelligence INTEGER NOT NULL DEFAULT 0,
            bonus_strength INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS level_rules (
            level INTEGER PRIMARY KEY CHECK(level>=1),
            total_exp INTEGER NOT NULL CHECK(total_exp>=0),
            stat_points INTEGER NOT NULL DEFAULT 0 CHECK(stat_points>=0)
        );
        CREATE TABLE IF NOT EXISTS item_definitions (
            item_id TEXT PRIMARY KEY, name TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('material','consumable','equipment')),
            slot TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
            hp_stat INTEGER NOT NULL DEFAULT 0, mental_stat INTEGER NOT NULL DEFAULT 0,
            agility INTEGER NOT NULL DEFAULT 0, intelligence INTEGER NOT NULL DEFAULT 0,
            strength INTEGER NOT NULL DEFAULT 0,
            max_stack INTEGER NOT NULL DEFAULT 999 CHECK(max_stack>=1),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
        );
        CREATE TABLE IF NOT EXISTS shop_products (
            item_id TEXT PRIMARY KEY REFERENCES item_definitions(item_id),
            stock INTEGER NOT NULL CHECK(stock>=-1),
            price INTEGER NOT NULL CHECK(price>=0),
            visible INTEGER NOT NULL DEFAULT 1 CHECK(visible IN (0,1))
        );
        CREATE TABLE IF NOT EXISTS character_inventory (
            character_id INTEGER NOT NULL REFERENCES characters(character_id) ON DELETE CASCADE,
            item_id TEXT NOT NULL REFERENCES item_definitions(item_id),
            quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0),
            PRIMARY KEY(character_id,item_id)
        );
        CREATE TABLE IF NOT EXISTS character_equipment (
            character_id INTEGER NOT NULL, slot TEXT NOT NULL,
            item_id TEXT NOT NULL,
            PRIMARY KEY(character_id,slot),
            FOREIGN KEY(character_id,item_id) REFERENCES character_inventory(character_id,item_id)
        );
        CREATE TABLE IF NOT EXISTS reports (
            report_id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_character_id INTEGER NOT NULL REFERENCES characters(character_id),
            author_name TEXT NOT NULL,
            author_job TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            report_type TEXT NOT NULL CHECK(report_type IN ('field','official','research')),
            status TEXT NOT NULL CHECK(status IN ('pending_office','pending_executive','returned','approved')),
            rejection_reason TEXT NOT NULL DEFAULT '',
            reviewer_character_id INTEGER REFERENCES characters(character_id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status,report_type);
        CREATE TABLE IF NOT EXISTS processed_report_ids (report_id INTEGER PRIMARY KEY REFERENCES reports(report_id));
        CREATE TABLE IF NOT EXISTS report_links (
            report_id INTEGER NOT NULL REFERENCES reports(report_id),
            source_report_id INTEGER NOT NULL REFERENCES reports(report_id),
            PRIMARY KEY(report_id,source_report_id)
        );
        CREATE TABLE IF NOT EXISTS crafting_recipes (
            recipe_id TEXT PRIMARY KEY, recipe_type TEXT NOT NULL,
            result_item_id TEXT NOT NULL REFERENCES item_definitions(item_id),
            result_quantity INTEGER NOT NULL, materials TEXT NOT NULL,
            success_rate INTEGER NOT NULL, gold_cost INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS consumable_effects (
            item_id TEXT PRIMARY KEY REFERENCES item_definitions(item_id),
            target TEXT NOT NULL CHECK(target IN ('hp','mental')),
            amount INTEGER NOT NULL CHECK(amount>0 AND amount<=999999)
        );
        CREATE TABLE IF NOT EXISTS game_sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, thread_id TEXT,
            story_id TEXT NOT NULL REFERENCES stories(story_id),
            scenario_version INTEGER NOT NULL, game_name TEXT NOT NULL,
            current_scene_id TEXT, status TEXT NOT NULL DEFAULT 'waiting',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            clear_completed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS session_members (
            session_id INTEGER NOT NULL REFERENCES game_sessions(session_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL, character_id INTEGER REFERENCES characters(character_id),
            role TEXT NOT NULL DEFAULT 'player', active INTEGER NOT NULL DEFAULT 1,
            joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(session_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS game_flags (
            session_id INTEGER NOT NULL REFERENCES game_sessions(session_id) ON DELETE CASCADE,
            scope TEXT NOT NULL, owner_id TEXT NOT NULL DEFAULT '',
            flag_key TEXT NOT NULL, value TEXT NOT NULL,
            PRIMARY KEY(session_id,scope,owner_id,flag_key)
        );
        CREATE TABLE IF NOT EXISTS vote_rounds (
            round_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES game_sessions(session_id) ON DELETE CASCADE,
            scene_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
            deadline TEXT, result_choice_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS votes (
            session_id INTEGER NOT NULL, round_id INTEGER NOT NULL REFERENCES vote_rounds(round_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL, choice_id TEXT NOT NULL,
            voted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(session_id,round_id,user_id),
            FOREIGN KEY(session_id,user_id) REFERENCES session_members(session_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS game_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES game_sessions(session_id) ON DELETE CASCADE,
            round_id TEXT, event_type TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS weekly_stories (
            week_start TEXT NOT NULL, story_id TEXT NOT NULL REFERENCES stories(story_id),
            display_order INTEGER NOT NULL,
            PRIMARY KEY(week_start,story_id)
        );
        CREATE TABLE IF NOT EXISTS board_messages (
            guild_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, message_id TEXT,
            last_week_start TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS story_clear_adjustments (
            story_id TEXT PRIMARY KEY REFERENCES stories(story_id),
            adjustment INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS operator_session_audit (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER, guild_id TEXT NOT NULL, operator_id TEXT NOT NULL,
            action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_status ON game_sessions(status);
        CREATE INDEX IF NOT EXISTS idx_sessions_story ON game_sessions(story_id);
        CREATE INDEX IF NOT EXISTS idx_votes_round ON votes(round_id);
        CREATE INDEX IF NOT EXISTS idx_member_character ON session_members(character_id);
        CREATE INDEX IF NOT EXISTS idx_item_category ON item_definitions(category);
        """)
        db.executescript("""
        CREATE TABLE IF NOT EXISTS character_chat_avatars (
            character_id INTEGER PRIMARY KEY REFERENCES characters(character_id) ON DELETE CASCADE,
            avatar_data BLOB NOT NULL, mime_type TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS character_chat_selection (
            guild_id TEXT NOT NULL, user_id TEXT NOT NULL,
            character_id INTEGER NOT NULL REFERENCES characters(character_id) ON DELETE CASCADE,
            PRIMARY KEY(guild_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS character_chat_channels (
            guild_id TEXT NOT NULL, channel_id TEXT NOT NULL,
            PRIMARY KEY(guild_id,channel_id)
        );
        """)
        # Existing SQLite tables are not altered by CREATE TABLE IF NOT EXISTS.
        # Apply additive, idempotent migrations without deleting user data.
        migrations = {
            'stories': {
                'availability': "TEXT NOT NULL DEFAULT 'weekly'",
                'clear_limit': 'INTEGER',
            },
            'item_definitions': {'sell_price': 'INTEGER'},
            'character_progress': {'spent_growth_points': 'INTEGER NOT NULL DEFAULT 0'},
            'game_sessions': {
                'clear_completed': 'INTEGER NOT NULL DEFAULT 0',
            },
        }
        for table, columns in migrations.items():
            existing = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
            for column, definition in columns.items():
                if column not in existing:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
    print('데이터베이스 초기화 완료!')

if __name__ == '__main__':
    init_db()

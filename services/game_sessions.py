"""Weekly story game session storage. Characters join separately."""
import sqlite3
from database import DB_PATH
from services.weekly_board import current_publication_week


def connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def _clear_count(db, story_id):
    from services.operator_sessions import count
    return count(db, story_id)


def available_stories():
    week = current_publication_week()
    with connect() as conn:
        rows = conn.execute('''
            SELECT s.*, w.display_order,
              (SELECT COUNT(*) FROM game_sessions g WHERE g.story_id=s.story_id
               AND g.status='finished' AND g.clear_completed=1) + COALESCE((SELECT adjustment FROM story_clear_adjustments a WHERE a.story_id=s.story_id),0) AS clear_count,
              (SELECT COUNT(*) FROM game_sessions g WHERE g.story_id=s.story_id
               AND g.status!='finished') AS running_count
            FROM stories s LEFT JOIN weekly_stories w
              ON w.story_id=s.story_id AND w.week_start=?
            WHERE s.enabled=1 AND
              (s.availability='permanent' OR (s.availability='weekly' AND w.story_id IS NOT NULL))
            ORDER BY CASE WHEN s.availability='weekly' THEN 0 ELSE 1 END,
                     w.display_order, s.title, s.story_id
        ''', (week,)).fetchall()
        return week, rows


def create_session(guild_id, channel_id, owner_id, story_id, name):
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        week = current_publication_week()
        story = conn.execute('SELECT * FROM stories WHERE story_id=? AND enabled=1',
                             (story_id,)).fetchone()
        if story is None:
            raise ValueError('활성화된 스토리가 아닙니다.')
        if story['availability'] == 'weekly':
            assigned = conn.execute('SELECT 1 FROM weekly_stories WHERE story_id=? AND week_start=?',
                                    (story_id, week)).fetchone()
            if not assigned:
                raise ValueError('이번 주 편성된 스토리가 아닙니다.')
        elif story['availability'] != 'permanent':
            raise ValueError('스토리 플레이 유형이 올바르지 않습니다.')
        running = conn.execute("SELECT 1 FROM game_sessions WHERE story_id=? AND status!='finished' LIMIT 1",
                               (story_id,)).fetchone()
        if running:
            raise ValueError('이 스토리는 이미 다른 세션에서 진행 중입니다. 종료 후 다시 시도해 주세요.')
        cleared = _clear_count(conn, story_id)
        if story['clear_limit'] is not None and cleared >= story['clear_limit']:
            raise ValueError(f'이 스토리는 클리어 횟수를 모두 사용했습니다. ({cleared}/{story["clear_limit"]})')
        cur = conn.execute('''INSERT INTO game_sessions
            (guild_id,channel_id,story_id,scenario_version,game_name,current_scene_id,status)
            VALUES (?,?,?,?,?,?,'waiting')''',
            (str(guild_id),str(channel_id),story_id,story['version'],name,story['start_scene_id']))
        session_id = cur.lastrowid
        conn.execute('''INSERT INTO session_members(session_id,user_id,role,active)
                        VALUES (?,?,'host',0)''', (session_id,str(owner_id)))
        return session_id, dict(story)


def set_thread(session_id, thread_id):
    with connect() as conn:
        conn.execute('UPDATE game_sessions SET thread_id=?,updated_at=CURRENT_TIMESTAMP WHERE session_id=?',
                     (str(thread_id),session_id))


def remove_session(session_id):
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT thread_id FROM game_sessions WHERE session_id=?', (session_id,)).fetchone()
        if row and row['thread_id'] is None:
            conn.execute('DELETE FROM session_members WHERE session_id=?', (session_id,))
            conn.execute('DELETE FROM game_sessions WHERE session_id=?', (session_id,))


def get_by_thread(thread_id):
    with connect() as conn:
        row = conn.execute('SELECT * FROM game_sessions WHERE thread_id=?', (str(thread_id),)).fetchone()
        return dict(row) if row else None


def members(session_id):
    from services.characters import get_members
    return get_members(session_id)


def leave_session(session_id, user_id):
    # Deactivate only this session membership.
    from services import story_engine
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        session = conn.execute('SELECT * FROM game_sessions WHERE session_id=?', (session_id,)).fetchone()
        if not session or session['status'] == 'finished':
            raise ValueError('이미 종료된 게이트입니다.')
        member = conn.execute("""SELECT c.name FROM session_members m
            JOIN characters c ON c.character_id=m.character_id
            WHERE m.session_id=? AND m.user_id=? AND m.active=1""",
            (session_id, str(user_id))).fetchone()
        if not member:
            raise ValueError('현재 이 게이트에 참가 중이지 않습니다.')
        conn.execute('UPDATE session_members SET active=0 WHERE session_id=? AND user_id=?',
                     (session_id, str(user_id)))
        conn.execute('DELETE FROM support_team_calls WHERE session_id=? AND user_id=?',
                     (session_id, str(user_id)))
        conn.execute('DELETE FROM actor_refusals WHERE user_id=? AND round_id IN (SELECT round_id FROM vote_rounds WHERE session_id=?)',
                     (str(user_id), session_id))
        conn.execute('DELETE FROM votes WHERE user_id=? AND round_id IN (SELECT round_id FROM vote_rounds WHERE session_id=?)',
                     (str(user_id), session_id))
        count = conn.execute("""SELECT COUNT(*) FROM session_members WHERE session_id=?
            AND active=1 AND character_id IS NOT NULL""", (session_id,)).fetchone()[0]
        state = session['status']
        if count == 0:
            conn.execute("UPDATE vote_rounds SET status='cancelled' WHERE session_id=? AND status IN ('open','actor_pending')", (session_id,))
            conn.execute('DELETE FROM pending_actors WHERE session_id=?', (session_id,))
            conn.execute("UPDATE game_sessions SET status='finished',updated_at=CURRENT_TIMESTAMP WHERE session_id=?", (session_id,))
            state = 'finished'
        elif state == 'actor_pending':
            from services.story_rules import eligible_actors
            pending = conn.execute('SELECT * FROM pending_actors WHERE session_id=?', (session_id,)).fetchone()
            eligible = eligible_actors(conn, session, pending['choice_id']) if pending else []
            refused = {r['user_id'] for r in conn.execute('SELECT user_id FROM actor_refusals WHERE round_id=?', (pending['round_id'],))} if pending else set()
            if not eligible or all(str(x['user_id']) in refused for x in eligible):
                conn.execute('DELETE FROM pending_actors WHERE session_id=?', (session_id,))
                conn.execute("UPDATE vote_rounds SET status='refused' WHERE round_id=?", (pending['round_id'],))
                conn.execute("INSERT INTO vote_rounds(session_id,scene_id,status) VALUES (?,?,'open')", (session_id,session['current_scene_id']))
                conn.execute("UPDATE game_sessions SET status='voting',updated_at=CURRENT_TIMESTAMP WHERE session_id=?", (session_id,))
                state = 'voting'
        return {'name': member['name'], 'remaining':count, 'status':state,
                'revote':session['status']=='actor_pending' and state=='voting'}

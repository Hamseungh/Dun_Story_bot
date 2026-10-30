"""Operator-only session recovery and story clear count overrides."""
import sqlite3
from database import DB_PATH


def connect():
    db = sqlite3.connect(str(DB_PATH), timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def count(db, story_id):
    completed = db.execute("SELECT COUNT(*) FROM game_sessions WHERE story_id=? AND status='finished' AND clear_completed=1", (story_id,)).fetchone()[0]
    adjustment = db.execute('SELECT adjustment FROM story_clear_adjustments WHERE story_id=?', (story_id,)).fetchone()
    return completed + (adjustment['adjustment'] if adjustment else 0)


def sessions(guild_id, include_finished=False):
    with connect() as db:
        rows = db.execute('''SELECT g.session_id,g.game_name,g.story_id,s.title,g.status,g.thread_id,g.updated_at,
                 (SELECT COUNT(*) FROM session_members m WHERE m.session_id=g.session_id AND m.active=1) AS members
                 FROM game_sessions g JOIN stories s ON s.story_id=g.story_id
                 WHERE g.guild_id=? AND (?=1 OR g.status!='finished')
                 ORDER BY CASE WHEN g.status='finished' THEN 1 ELSE 0 END,g.session_id DESC LIMIT 25''',
                 (str(guild_id), int(include_finished))).fetchall()
        return [dict(r) for r in rows]


def story_status(story_id):
    with connect() as db:
        story = db.execute('SELECT * FROM stories WHERE story_id=?', (story_id,)).fetchone()
        if not story:
            raise ValueError('해당 스토리를 찾을 수 없습니다.')
        active = db.execute("SELECT session_id,guild_id,thread_id,status FROM game_sessions WHERE story_id=? AND status!='finished' ORDER BY session_id", (story_id,)).fetchall()
        return dict(story), count(db, story_id), [dict(x) for x in active]


def force_finish(guild_id, session_id, operator_id):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        session = db.execute('SELECT * FROM game_sessions WHERE session_id=? AND guild_id=?', (session_id,str(guild_id))).fetchone()
        if session is None:
            raise ValueError('이 서버에서 해당 세션을 찾을 수 없습니다.')
        if session['status']=='finished':
            raise ValueError('이미 종료된 세션입니다.')
        db.execute("UPDATE vote_rounds SET status='cancelled' WHERE session_id=? AND status IN ('open','actor_pending')", (session_id,))
        db.execute('DELETE FROM pending_actors WHERE session_id=?', (session_id,))
        db.execute("UPDATE game_sessions SET status='finished',clear_completed=0,updated_at=CURRENT_TIMESTAMP WHERE session_id=?", (session_id,))
        db.execute('INSERT INTO operator_session_audit(session_id,guild_id,operator_id,action,detail) VALUES (?,?,?,?,?)',
                   (session_id,str(guild_id),str(operator_id),'force_finish',session['status']))
        return dict(session)


def set_clear_count(story_id, target, operator_id, guild_id):
    if target < 0:
        raise ValueError('클리어 횟수는 0 이상이어야 합니다.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        story = db.execute('SELECT story_id FROM stories WHERE story_id=?', (story_id,)).fetchone()
        if story is None:
            raise ValueError('해당 스토리를 찾을 수 없습니다.')
        before = count(db, story_id)
        completed = db.execute("SELECT COUNT(*) FROM game_sessions WHERE story_id=? AND status='finished' AND clear_completed=1", (story_id,)).fetchone()[0]
        db.execute('''INSERT INTO story_clear_adjustments(story_id,adjustment) VALUES (?,?)
                      ON CONFLICT(story_id) DO UPDATE SET adjustment=excluded.adjustment''', (story_id,target-completed))
        db.execute('INSERT INTO operator_session_audit(session_id,guild_id,operator_id,action,detail) VALUES (NULL,?,?,?,?)',
                   (str(guild_id),str(operator_id),'clear_count',f'{story_id}: {before} -> {target}'))
        return before

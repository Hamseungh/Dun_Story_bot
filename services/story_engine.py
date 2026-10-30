"""Scene and vote engine. Database transactions serialize votes and transitions."""
import random
import sqlite3
from database import DB_PATH


def connect():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    return db


def migrate():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS story_scenes (
          story_id TEXT NOT NULL, version INTEGER NOT NULL, scene_id TEXT NOT NULL,
          title TEXT NOT NULL, content TEXT NOT NULL, scene_type TEXT NOT NULL,
          next_scene_id TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(story_id,version,scene_id));
        CREATE TABLE IF NOT EXISTS story_choices (
          story_id TEXT NOT NULL, version INTEGER NOT NULL, choice_id TEXT NOT NULL,
          scene_id TEXT NOT NULL, text TEXT NOT NULL, display_order INTEGER NOT NULL,
          next_scene_id TEXT NOT NULL,
          PRIMARY KEY(story_id,version,choice_id),
          UNIQUE(story_id,version,scene_id,display_order),
          FOREIGN KEY(story_id,version,scene_id)
            REFERENCES story_scenes(story_id,version,scene_id));
        CREATE TABLE IF NOT EXISTS pending_actors (
          session_id INTEGER PRIMARY KEY REFERENCES game_sessions(session_id) ON DELETE CASCADE,
          round_id INTEGER NOT NULL UNIQUE REFERENCES vote_rounds(round_id),
          choice_id TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS actor_refusals (
          round_id INTEGER NOT NULL REFERENCES vote_rounds(round_id) ON DELETE CASCADE,
          user_id TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(round_id, user_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_round
          ON vote_rounds(session_id) WHERE status='open';
        CREATE TABLE IF NOT EXISTS support_team_calls (
          session_id INTEGER NOT NULL REFERENCES game_sessions(session_id) ON DELETE CASCADE,
          user_id TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(session_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS choice_roll_results (
          round_id INTEGER PRIMARY KEY REFERENCES vote_rounds(round_id),
          session_id INTEGER NOT NULL REFERENCES game_sessions(session_id),
          choice_id TEXT NOT NULL, roll INTEGER NOT NULL, success_rate INTEGER NOT NULL,
          success INTEGER NOT NULL, destination TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        ''')
        columns = {r['name'] for r in db.execute('PRAGMA table_info(story_choices)')}
        if 'success_rate' not in columns:
            db.execute('ALTER TABLE story_choices ADD COLUMN success_rate INTEGER')
        if 'success_scene_id' not in columns:
            db.execute("ALTER TABLE story_choices ADD COLUMN success_scene_id TEXT NOT NULL DEFAULT ''")
        if 'failure_scene_id' not in columns:
            db.execute("ALTER TABLE story_choices ADD COLUMN failure_scene_id TEXT NOT NULL DEFAULT ''")


def _scene(db, session):
    row = db.execute('SELECT * FROM story_scenes WHERE story_id=? AND version=? AND scene_id=?',
        (session['story_id'], session['scenario_version'], session['current_scene_id'])).fetchone()
    if not row:
        raise ValueError('현재 장면이 동기화되어 있지 않습니다. 스토리 버전과 Scenes 탭을 확인하세요.')
    return dict(row)


def _choices(db, session, scene):
    from services.story_rules import visible_choices
    choices = [dict(x) for x in db.execute('''SELECT * FROM story_choices
       WHERE story_id=? AND version=? AND scene_id=? ORDER BY display_order''',
       (session['story_id'],session['scenario_version'],scene['scene_id']))]
    return visible_choices(db, session, choices)


def _members(db, sid):
    return [str(x['user_id']) for x in db.execute('''SELECT m.user_id FROM session_members m
        JOIN character_progress p ON p.character_id=m.character_id
        WHERE m.session_id=? AND m.active=1 AND m.character_id IS NOT NULL
        AND p.current_hp>0 AND p.current_mental>0''', (sid,))]


def _active_members(db, sid):
    # All joined members, including incapacitated ones.
    return [str(row['user_id']) for row in db.execute('''SELECT user_id FROM session_members
        WHERE session_id=? AND active=1 AND character_id IS NOT NULL''', (sid,))]


def _vote_tally(db, round_id, members):
    if not members:
        return {}
    placeholders = ','.join('?' for _ in members)
    return {row['choice_id']: row['n'] for row in db.execute(
        f'''SELECT choice_id,COUNT(*) AS n FROM votes
        WHERE round_id=? AND user_id IN ({placeholders}) GROUP BY choice_id''',
        (round_id, *members))}


def _vote_count(db, round_id, members):
    if not round_id or not members:
        return 0
    placeholders = ','.join('?' for _ in members)
    return db.execute(f'''SELECT COUNT(*) FROM votes WHERE round_id=?
        AND user_id IN ({placeholders})''', (round_id, *members)).fetchone()[0]


def _incapacitated(db, sid):
    return [dict(row) for row in db.execute('''SELECT m.user_id,c.name,
        p.current_hp,p.current_mental FROM session_members m
        JOIN characters c ON c.character_id=m.character_id
        JOIN character_progress p ON p.character_id=m.character_id
        WHERE m.session_id=? AND m.active=1 AND m.character_id IS NOT NULL
        AND (p.current_hp<=0 OR p.current_mental<=0)''', (sid,))]


def _warning(db, session, choices=None):
    if session['status'] not in ('voting','actor_pending','reading'):
        return None
    members = _active_members(db, session['session_id'])
    if members and not _members(db, session['session_id']):
        return '모든 참가자의 체력 또는 정신력이 0입니다. 회복 아이템을 사용하거나 /지원팀호출을 이용해 주세요.'
    if session['status']=='voting' and choices and not any(x['available'] for x in choices):
        return '현재 선택 가능한 선택지가 없습니다. 조건을 확인하거나 아이템으로 회복해 주세요.'
    if session['status']=='actor_pending':
        pending = db.execute('SELECT choice_id FROM pending_actors WHERE session_id=?',
                             (session['session_id'],)).fetchone()
        if pending:
            from services.story_rules import eligible_actors
            if not eligible_actors(db, session, pending['choice_id']):
                return '현재 행동 가능한 담당자가 없습니다. 회복 후 /행동 또는 /행동거부를 사용해 주세요.'
    return None


def _round(db, sid):
    return db.execute('SELECT * FROM vote_rounds WHERE session_id=? AND status=? ORDER BY round_id DESC LIMIT 1', (sid,'open')).fetchone()


def _display(db, session):
    scene = _scene(db, session)
    choices = _choices(db,session,scene)
    rnd = _round(db,session['session_id'])
    members = _members(db,session['session_id'])
    count = _vote_count(db, rnd['round_id'], members) if rnd else 0
    return {'scene':scene,'choices':choices,'round':dict(rnd) if rnd else None,
            'voted':count,'members':len(members),'status':session['status'],
            'warning':_warning(db,session,choices),'incapacitated':_incapacitated(db,session['session_id'])}


def current(session_id):
    with connect() as db:
        session = db.execute('SELECT * FROM game_sessions WHERE session_id=?',(session_id,)).fetchone()
        if not session: raise ValueError('게임을 찾을 수 없습니다.')
        if session['status']=='waiting': raise ValueError('아직 시작하지 않았습니다. /진행을 사용하세요.')
        return _display(db,session)


def progress(session_id, user_id):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        session = db.execute('SELECT * FROM game_sessions WHERE session_id=?',(session_id,)).fetchone()
        if not session: raise ValueError('게임을 찾을 수 없습니다.')
        if str(user_id) not in _active_members(db,session_id): raise ValueError('게임 참가자만 진행할 수 있습니다.')
        if session['status']=='finished': raise ValueError('이미 종료된 게임입니다.')
        if session['status']=='waiting':
            if not _active_members(db,session_id): raise ValueError('먼저 캐릭터로 참가해 주세요.')
            scene = _scene(db,session)
            choices = _choices(db,session,scene)
            status = 'ending' if scene['scene_type']=='ending' else ('voting' if choices else 'reading')
            db.execute('UPDATE game_sessions SET status=?,updated_at=CURRENT_TIMESTAMP WHERE session_id=?',(status,session_id))
            if not choices and scene['scene_type']!='ending' and not scene['next_scene_id']:
                raise ValueError('현재 조건을 충족하는 선택지가 없습니다.')
            # Keep the scene open even if everyone is incapacitated or all choices are locked.
            if choices: db.execute('INSERT INTO vote_rounds(session_id,scene_id,status) VALUES (?,?,?)',(session_id,scene['scene_id'],'open'))
            session = db.execute('SELECT * FROM game_sessions WHERE session_id=?',(session_id,)).fetchone()
            return {'kind':'scene','view':_display(db,session)}
        if session['status']=='actor_pending': raise ValueError('행동 담당자를 기다리고 있습니다. 조건을 충족한 참가자가 /행동을 사용해 주세요.')
        if session['status']=='voting': raise ValueError('선택지가 있습니다. /투표를 사용하세요.')
        if session['status']=='ending':
            db.execute("UPDATE game_sessions SET status='finished',clear_completed=1,updated_at=CURRENT_TIMESTAMP WHERE session_id=? AND status='ending'",(session_id,))
            return {'kind':'finished'}
        if session['status']!='reading': raise ValueError('진행할 수 없는 게임 상태입니다.')
        scene = _scene(db,session)
        if not scene['next_scene_id']: raise ValueError('다음 장면이 지정되지 않았습니다.')
        db.execute('UPDATE game_sessions SET current_scene_id=?,updated_at=CURRENT_TIMESTAMP WHERE session_id=?',
                   (scene['next_scene_id'],session_id))
        session = db.execute('SELECT * FROM game_sessions WHERE session_id=?',(session_id,)).fetchone()
        next_scene = _scene(db,session)
        choices = _choices(db,session,next_scene)
        if not choices and next_scene['scene_type']!='ending' and not next_scene['next_scene_id']:
            raise ValueError('다음 장면에서 조건을 충족하는 선택지가 없습니다.')
        status = 'ending' if next_scene['scene_type']=='ending' else ('voting' if choices else 'reading')
        db.execute('UPDATE game_sessions SET status=?,updated_at=CURRENT_TIMESTAMP WHERE session_id=?',(status,session_id))
        if choices: db.execute('INSERT INTO vote_rounds(session_id,scene_id,status) VALUES (?,? ,?)',(session_id,next_scene['scene_id'],'open'))
        session = db.execute('SELECT * FROM game_sessions WHERE session_id=?',(session_id,)).fetchone()
        return {'kind':'scene','view':_display(db,session)}


def vote(session_id,user_id,number):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        session = db.execute('SELECT * FROM game_sessions WHERE session_id=?',(session_id,)).fetchone()
        if not session or session['status']!='voting': raise ValueError('현재 투표 중인 장면이 아닙니다.')
        members = _members(db,session_id)
        if str(user_id) not in members: raise ValueError('체력 또는 정신력이 0인 캐릭터는 투표할 수 없습니다. 아이템으로 회복해 주세요.')
        scene = _scene(db,session)
        choices = _choices(db,session,scene)
        picked = next((x for x in choices if x['display_order']==int(number)),None)
        if picked is None: raise ValueError('존재하지 않는 선택지 번호입니다.')
        if not picked['available']: raise ValueError('조건을 충족하지 못해 투표할 수 없는 선택지입니다.')
        rnd = _round(db,session_id)
        if not rnd: raise ValueError('투표 라운드를 찾을 수 없습니다.')
        db.execute('''INSERT INTO votes(session_id,round_id,user_id,choice_id) VALUES (?,?,?,?)
            ON CONFLICT(session_id,round_id,user_id) DO UPDATE SET
            choice_id=excluded.choice_id,voted_at=CURRENT_TIMESTAMP''',
            (session_id,rnd['round_id'],str(user_id),picked['choice_id']))
        cast = _vote_count(db, rnd['round_id'], members)
        if cast<len(members): return {'kind':'pending','voted':cast,'members':len(members)}
        tally = _vote_tally(db, rnd['round_id'], members)
        highest = max(tally.values())
        winners = [cid for cid,n in tally.items() if n==highest]
        if len(winners)>1:
            db.execute("UPDATE vote_rounds SET status='tied' WHERE round_id=?",(rnd['round_id'],))
            db.execute('INSERT INTO vote_rounds(session_id,scene_id,status) VALUES (?,?,?)',(session_id,scene['scene_id'],'open'))
            return {'kind':'tie','tally':tally,'choices':choices,'members':len(members)}
        winner = next(x for x in choices if x['choice_id']==winners[0])
        from services.story_rules import needs_actor, eligible_actors
        if needs_actor(db,session,winner['choice_id']):
            eligible = eligible_actors(db,session,winner['choice_id'])
            if not eligible:
                raise ValueError('선택지 조건을 모두 만족하는 행동 담당 캐릭터가 없습니다. 조건을 확인해 주세요.')
            db.execute("UPDATE vote_rounds SET status='actor_pending',result_choice_id=? WHERE round_id=?",
                       (winner['choice_id'],rnd['round_id']))
            db.execute('INSERT INTO pending_actors(session_id,round_id,choice_id) VALUES (?,?,?)',
                       (session_id,rnd['round_id'],winner['choice_id']))
            db.execute("UPDATE game_sessions SET status='actor_pending',updated_at=CURRENT_TIMESTAMP WHERE session_id=?",(session_id,))
            return {'kind':'actor_pending','winner':winner,'eligible':eligible,'tally':tally}
        return _advance(db,session,winner,rnd['round_id'],tally,None)


def _advance(db,session,winner,round_id,tally,actor_id):
    from services.story_rules import apply
    rate=winner.get('success_rate')
    check=None
    destination=winner['next_scene_id']
    if rate is not None:
        roll=random.SystemRandom().randint(1,100)
        success=roll<=int(rate)
        destination=(winner['success_scene_id'] if success else winner['failure_scene_id'])
        if not destination:
            raise ValueError('성공/실패 이동 장면이 지정되지 않았습니다.')
        db.execute('''INSERT INTO choice_roll_results
          (round_id,session_id,choice_id,roll,success_rate,success,destination)
          VALUES (?,?,?,?,?,?,?)''',
          (round_id,session['session_id'],winner['choice_id'],roll,int(rate),int(success),destination))
        check={'roll':roll,'rate':int(rate),'success':success,'destination':destination}
    outcome=('success' if check['success'] else 'failure') if check else 'always'
    effects=apply(db,session,winner['choice_id'],actor_id,outcome=outcome)

    db.execute("UPDATE vote_rounds SET status='closed',result_choice_id=? WHERE round_id=?",(winner['choice_id'],round_id))
    db.execute('DELETE FROM actor_refusals WHERE round_id=?',(round_id,))
    db.execute('DELETE FROM pending_actors WHERE session_id=?',(session['session_id'],))
    db.execute('UPDATE game_sessions SET current_scene_id=?,updated_at=CURRENT_TIMESTAMP WHERE session_id=?',
               (destination,session['session_id']))
    session=db.execute('SELECT * FROM game_sessions WHERE session_id=?',(session['session_id'],)).fetchone()
    next_scene=_scene(db,session)
    next_choices=_choices(db,session,next_scene)
    if not next_choices and next_scene['scene_type']!='ending' and not next_scene['next_scene_id']:
        raise ValueError('다음 장면에서 조건을 충족하는 선택지가 없습니다.')
    status='ending' if next_scene['scene_type']=='ending' else ('voting' if next_choices else 'reading')
    db.execute('UPDATE game_sessions SET status=?,updated_at=CURRENT_TIMESTAMP WHERE session_id=?',(status,session['session_id']))
    if next_choices:
        db.execute('INSERT INTO vote_rounds(session_id,scene_id,status) VALUES (?,?,?)',
                   (session['session_id'],next_scene['scene_id'],'open'))
    session=db.execute('SELECT * FROM game_sessions WHERE session_id=?',(session['session_id'],)).fetchone()
    return {'kind':'advanced','winner':winner,'tally':tally,'effects':effects,'check':check,'view':_display(db,session)}


def actor_status(session_id):
    with connect() as db:
        session=db.execute('SELECT * FROM game_sessions WHERE session_id=?',(session_id,)).fetchone()
        if not session or session['status']!='actor_pending':
            raise ValueError('행동 담당자를 기다리는 상태가 아닙니다.')
        pending=db.execute('SELECT * FROM pending_actors WHERE session_id=?',(session_id,)).fetchone()
        from services.story_rules import eligible_actors
        return {'eligible':eligible_actors(db,session,pending['choice_id']), 'choice_id':pending['choice_id']}


def accept_actor(session_id,user_id):
    # Eligible player volunteers as actor. Exactly one transition is committed.
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        session=db.execute('SELECT * FROM game_sessions WHERE session_id=?',(session_id,)).fetchone()
        if not session or session['status']!='actor_pending':
            raise ValueError('현재 행동 담당자를 지정할 수 없습니다.')
        pending=db.execute('SELECT * FROM pending_actors WHERE session_id=?',(session_id,)).fetchone()
        if not pending:
            raise ValueError('대기 중인 선택지를 찾지 못했습니다.')
        from services.story_rules import eligible_actors
        actor=next((a for a in eligible_actors(db,session,pending['choice_id'])
                    if a['user_id']==str(user_id)),None)
        if actor is None:
            raise ValueError('이 선택지의 조건을 충족한 참가자만 행동을 맡을 수 있습니다.')
        winner=db.execute('''SELECT * FROM story_choices WHERE story_id=? AND version=? AND choice_id=?''',
                          (session['story_id'],session['scenario_version'],pending['choice_id'])).fetchone()
        if not winner:
            raise ValueError('선택지 데이터를 찾지 못했습니다.')
        result=_advance(db,session,dict(winner),pending['round_id'],{},actor['character_id'])
        result['actor']=actor
        return result



def refuse_actor(session_id, user_id):
    """Record an eligible participant's refusal; revote only when all refuse.

    Serialized by BEGIN IMMEDIATE, so concurrent /행동 and /행동거부 cannot
    advance and restart the same round simultaneously.
    """
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        session = db.execute('SELECT * FROM game_sessions WHERE session_id=?', (session_id,)).fetchone()
        if not session or session['status'] != 'actor_pending':
            raise ValueError('현재 행동 담당자를 기다리는 상태가 아닙니다.')
        if str(user_id) not in _members(db, session_id):
            raise ValueError('체력 또는 정신력이 0인 캐릭터는 행동을 거부할 수 없습니다. 회복 후 다시 시도해 주세요.')
        pending = db.execute('SELECT * FROM pending_actors WHERE session_id=?', (session_id,)).fetchone()
        if not pending:
            raise ValueError('대기 중인 선택지를 찾지 못했습니다.')
        from services.story_rules import eligible_actors
        eligible = eligible_actors(db, session, pending['choice_id'])
        eligible_ids = {str(a['user_id']) for a in eligible}
        if str(user_id) not in eligible_ids:
            raise ValueError('이 선택지의 행동을 맡을 수 있는 참가자만 거부할 수 있습니다.')
        old_round = db.execute('SELECT * FROM vote_rounds WHERE round_id=? AND session_id=?',
                               (pending['round_id'], session_id)).fetchone()
        if not old_round or old_round['status'] != 'actor_pending':
            raise ValueError('행동 대기 중인 투표 라운드를 찾지 못했습니다.')
        already = db.execute('SELECT 1 FROM actor_refusals WHERE round_id=? AND user_id=?',
                             (pending['round_id'], str(user_id))).fetchone()
        if already:
            raise ValueError('이미 행동을 거부했습니다. 다른 행동 가능 참가자의 결정을 기다려 주세요.')
        db.execute('INSERT INTO actor_refusals(round_id,user_id) VALUES (?,?)',
                   (pending['round_id'], str(user_id)))
        refused = {r['user_id'] for r in db.execute(
            'SELECT user_id FROM actor_refusals WHERE round_id=?', (pending['round_id'],))}
        remaining = eligible_ids - refused
        if remaining:
            return {'kind': 'refusal_pending', 'refused': len(eligible_ids) - len(remaining),
                    'total': len(eligible_ids), 'remaining': sorted(remaining)}
        scene = _scene(db, session)
        db.execute('DELETE FROM actor_refusals WHERE round_id=?', (pending['round_id'],))
        db.execute('DELETE FROM pending_actors WHERE session_id=?', (session_id,))
        db.execute("UPDATE vote_rounds SET status='refused' WHERE round_id=?", (pending['round_id'],))
        db.execute("UPDATE game_sessions SET status='voting',updated_at=CURRENT_TIMESTAMP WHERE session_id=?", (session_id,))
        db.execute("INSERT INTO vote_rounds(session_id,scene_id,status) VALUES (?,?, 'open')",
                   (session_id, scene['scene_id']))
        refreshed = db.execute('SELECT * FROM game_sessions WHERE session_id=?', (session_id,)).fetchone()
        return {'kind': 'revote', 'view': _display(db, refreshed)}


def vote_status(session_id):
    with connect() as db:
        session = db.execute('SELECT * FROM game_sessions WHERE session_id=?',(session_id,)).fetchone()
        if not session or session['status']!='voting': raise ValueError('현재 투표 중이 아닙니다.')
        rnd = _round(db,session_id)
        members = _members(db,session_id)
        voted = {str(r['user_id']) for r in db.execute('SELECT user_id FROM votes WHERE round_id=?',(rnd['round_id'],))} & set(members)
        return {'voted':len(voted),'total':len(members),'remaining':[u for u in members if u not in voted]}


def call_support_team(session_id, user_id):
    """All active session participants (including incapacitated ones) must call.

    The session is ended atomically on the final call; prior story effects remain.
    """
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        session = db.execute('SELECT * FROM game_sessions WHERE session_id=?', (session_id,)).fetchone()
        if not session or session['status'] in ('waiting','finished'):
            raise ValueError('현재 진행 중인 스토리가 아닙니다.')
        members = _active_members(db, session_id)
        if str(user_id) not in members:
            raise ValueError('게임에 참가 중인 캐릭터만 지원팀을 호출할 수 있습니다.')
        if db.execute('SELECT 1 FROM support_team_calls WHERE session_id=? AND user_id=?',
                      (session_id,str(user_id))).fetchone():
            raise ValueError('이미 지원팀을 호출했습니다. 다른 참가자를 기다려 주세요.')
        db.execute('INSERT INTO support_team_calls(session_id,user_id) VALUES (?,?)',
                   (session_id,str(user_id)))
        called = {str(r['user_id']) for r in db.execute(
            'SELECT user_id FROM support_team_calls WHERE session_id=?', (session_id,))}
        remaining = [uid for uid in members if uid not in called]
        if remaining:
            return {'kind':'pending','called':len(members)-len(remaining),
                    'total':len(members),'remaining':remaining}
        db.execute("UPDATE vote_rounds SET status='cancelled' WHERE session_id=? AND status IN ('open','actor_pending')",
                   (session_id,))
        db.execute('DELETE FROM pending_actors WHERE session_id=?', (session_id,))
        db.execute("UPDATE game_sessions SET status='finished',updated_at=CURRENT_TIMESTAMP WHERE session_id=?",
                   (session_id,))
        return {'kind':'finished','total':len(members)}

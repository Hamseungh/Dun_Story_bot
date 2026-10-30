"""Versioned conditions and transactional effects for story choices."""
import json
from services.story_engine import connect

STATS = {'hp_stat': 'bonus_hp', 'mental_stat': 'bonus_mental', 'agility': 'bonus_agility', 'intelligence': 'bonus_intelligence', 'strength': 'bonus_strength'}
OPS = {'==', '!=', '>=', '<=', '>', '<'}
CONDITION_TYPES = {'stat', 'item', 'flag', 'grade', 'job'}
EFFECT_TYPES = {'hp', 'mental', 'gold', 'exp', 'item_add', 'item_remove', 'flag_set'}


def migrate():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS story_rulesets (story_id TEXT NOT NULL, version INTEGER NOT NULL, PRIMARY KEY(story_id,version));
        CREATE TABLE IF NOT EXISTS story_conditions (
          story_id TEXT NOT NULL, version INTEGER NOT NULL, condition_id TEXT NOT NULL,
          choice_id TEXT NOT NULL, condition_type TEXT NOT NULL, target TEXT NOT NULL,
          operator TEXT NOT NULL, value TEXT NOT NULL, scope TEXT NOT NULL,
          PRIMARY KEY(story_id,version,condition_id),
          FOREIGN KEY(story_id,version,choice_id) REFERENCES story_choices(story_id,version,choice_id));
        CREATE TABLE IF NOT EXISTS story_effects (
          story_id TEXT NOT NULL, version INTEGER NOT NULL, effect_id TEXT NOT NULL,
          choice_id TEXT NOT NULL, effect_type TEXT NOT NULL, target TEXT NOT NULL,
          value TEXT NOT NULL, scope TEXT NOT NULL,
          PRIMARY KEY(story_id,version,effect_id),
          FOREIGN KEY(story_id,version,choice_id) REFERENCES story_choices(story_id,version,choice_id));
        ''')
        columns = {r['name'] for r in db.execute('PRAGMA table_info(story_effects)')}
        if 'result' not in columns:
            db.execute("ALTER TABLE story_effects ADD COLUMN result TEXT NOT NULL DEFAULT 'always'")


def _compare(left, operator, right):
    if operator == '==': return left == right
    if operator == '!=': return left != right
    if operator == '>=': return left >= right
    if operator == '<=': return left <= right
    if operator == '>': return left > right
    if operator == '<': return left < right
    raise ValueError('지원하지 않는 조건 연산자입니다.')


def _participants(db, session_id):
    return db.execute('''SELECT c.*,p.* FROM session_members m
        JOIN characters c ON c.character_id=m.character_id
        JOIN character_progress p ON p.character_id=c.character_id
        WHERE m.session_id=? AND m.active=1 AND m.character_id IS NOT NULL
        ORDER BY m.user_id''', (session_id,)).fetchall()


def _stat(db, char, key):
    bonus = db.execute(f'''SELECT COALESCE(SUM(d.{key}),0) FROM character_equipment e
        JOIN character_inventory i ON i.character_id=e.character_id AND i.item_id=e.item_id AND i.quantity>0
        JOIN item_definitions d ON d.item_id=e.item_id AND d.slot=e.slot AND d.category='equipment'
        WHERE e.character_id=?''', (char['character_id'],)).fetchone()[0]
    return int(char[key]) + int(char[STATS[key]]) + int(bonus)


def _conditions(db, session, choice_id):
    return db.execute('''SELECT * FROM story_conditions WHERE story_id=? AND version=? AND choice_id=? ORDER BY condition_id''',
        (session['story_id'],session['scenario_version'],choice_id)).fetchall()


def allowed(db, session, choice_id):
    """Each condition can be met by any active member, not necessarily the same member."""
    members = _participants(db, session['session_id'])
    for cond in _conditions(db,session,choice_id):
        kind, target, op, raw = (cond[k] for k in ('condition_type','target','operator','value'))
        if kind == 'flag':
            flag = db.execute('''SELECT value FROM game_flags WHERE session_id=? AND scope='session' AND owner_id='' AND flag_key=?''',
                (session['session_id'],target)).fetchone()
            left = flag['value'] if flag else 'false'
            if not _compare(left,op,raw): return False
            continue
        found = False
        for member in members:
            if kind == 'stat': left, right = _stat(db,member,target), int(raw)
            elif kind == 'item':
                item = db.execute('SELECT quantity FROM character_inventory WHERE character_id=? AND item_id=?',
                    (member['character_id'],target)).fetchone()
                left, right = (item['quantity'] if item else 0),int(raw)
            elif kind in ('grade','job'): left, right = member[kind],raw
            else: raise ValueError('지원하지 않는 조건입니다.')
            if _compare(left,op,right):
                found=True
                break
        if not found: return False
    return True


STAT_LABELS = {'hp_stat': '체력', 'mental_stat': '정신력', 'agility': '민첩', 'intelligence': '지능', 'strength': '힘'}
OP_LABELS = {'>=': '이상', '<=': '이하', '>': '초과', '<': '미만', '==': '같음', '!=': '다름'}


def _condition_label(db, condition):
    kind = condition['condition_type']
    target = condition['target']
    op = condition['operator']
    raw = str(condition['value'])
    operator = OP_LABELS.get(op, op)
    if kind == 'stat':
        return f"{STAT_LABELS.get(target, target)} {raw} {operator}"
    if kind == 'item':
        item = db.execute('SELECT name FROM item_definitions WHERE item_id=?', (target,)).fetchone()
        name = item['name'] if item else target
        return f"{name} {raw}개 {operator} 보유"
    if kind == 'flag':
        status = '획득' if raw.lower() in ('true', '1') else '미획득' if raw.lower() in ('false', '0') else raw
        return f"{target} {status}" if op == '==' else f"{target} {status} 아님"
    if kind in ('grade', 'job'):
        prefix = '등급' if kind == 'grade' else '직군'
        return f"{prefix} {raw} {operator}"
    return f"{target} {op} {raw}"


def visible_choices(db, session, choices):
    result = []
    for choice in choices:
        conditions = _conditions(db, session, choice['choice_id'])
        labels = [_condition_label(db, cond) for cond in conditions if cond['condition_type'] != 'flag']
        if choice.get('success_rate') is not None:
            labels.append(f"{int(choice['success_rate'])}%")
        result.append(dict(choice,
            available=bool(allowed(db, session, choice['choice_id'])),
            condition_text=' / '.join(labels)))
    return result


def _resource_cap(db, char, kind):
    stat = 'hp_stat' if kind == 'hp' else 'mental_stat'
    # Base and growth are stat points; equipment bonuses are direct resource points.
    multiplier = 20 if kind == 'hp' else 10
    growth_col = STATS[stat]
    base = int(char[stat] or 0)
    growth = int(char[growth_col] or 0)
    equipment = _stat(db, char, stat) - base - growth
    return max(0, (base + growth) * multiplier + equipment)


def eligible_actors(db, session, choice_id):
    # Return members who individually satisfy all character-related conditions.
    # Session flags are evaluated separately through allowed().
    if not allowed(db, session, choice_id):
        return []
    result = []
    for member in _participants(db, session['session_id']):
        ok = True
        for cond in _conditions(db, session, choice_id):
            kind, target, op, raw = (cond[k] for k in ('condition_type','target','operator','value'))
            if kind == 'flag':
                continue
            if kind == 'stat':
                left, right = _stat(db, member, target), int(raw)
            elif kind == 'item':
                row = db.execute('SELECT quantity FROM character_inventory WHERE character_id=? AND item_id=?',
                                 (member['character_id'],target)).fetchone()
                left, right = (row['quantity'] if row else 0), int(raw)
            else:
                left, right = member[kind], raw
            if not _compare(left,op,right):
                ok = False
                break
        if ok:
            result.append({'user_id':str(member['user_id']), 'character_id':member['character_id'], 'name':member['name']})
    return result


def needs_actor(db, session, choice_id):
    return bool(db.execute('''SELECT 1 FROM story_effects WHERE story_id=? AND version=?
        AND choice_id=? AND scope='selected_character' LIMIT 1''',
        (session['story_id'],session['scenario_version'],choice_id)).fetchone())


def apply(db, session, choice_id, actor_id=None, outcome="always"):
    """Must be called inside vote()'s BEGIN IMMEDIATE transaction, before advancing."""
    effects = db.execute('''SELECT * FROM story_effects WHERE story_id=? AND version=? AND choice_id=? ORDER BY effect_id''',
        (session['story_id'],session['scenario_version'],choice_id)).fetchall()
    effects = [e for e in effects if e['result'] == 'always' or e['result'] == outcome]
    members = _participants(db,session['session_id'])
    log=[]
    for e in effects:
        kind, target, raw = (e[k] for k in ('effect_type','target','value'))
        if kind == 'flag_set':
            db.execute('''INSERT INTO game_flags(session_id,scope,owner_id,flag_key,value) VALUES (?,'session','',?,?)
                ON CONFLICT(session_id,scope,owner_id,flag_key) DO UPDATE SET value=excluded.value''',
                (session['session_id'],target,raw))
            log.append({'type':kind,'target':target,'value':raw})
            continue
        delta=int(raw)
        if e['scope'] == 'selected_character':
            if actor_id is None:
                raise ValueError('행동 담당 캐릭터를 먼저 지정해야 합니다.')
            recipients = [m for m in members if m['character_id'] == actor_id]
            if len(recipients) != 1:
                raise ValueError('행동 담당 캐릭터가 현재 참가 중이지 않습니다.')
        else:
            recipients = members
        for member in recipients:
            cid=member['character_id']
            actual_delta = None
            item_name = None
            if kind in ('hp','mental'):
                column = 'current_hp' if kind=='hp' else 'current_mental'
                cap=_resource_cap(db,member,kind)
                current=db.execute(f'SELECT {column} FROM character_progress WHERE character_id=?',(cid,)).fetchone()[0]
                value=max(0,min(cap,current+delta))
                db.execute(f'UPDATE character_progress SET {column}=? WHERE character_id=?',(value,cid))
                actual_delta = value - current
            elif kind in ('gold','exp'):
                current=db.execute(f'SELECT {kind} FROM character_progress WHERE character_id=?',(cid,)).fetchone()[0]
                value=max(0,current+delta)
                db.execute(f'UPDATE character_progress SET {kind}=? WHERE character_id=?',(value,cid))
                if kind == 'exp':
                    from services.levels import recalculate
                    recalculate(db, cid, value)
                actual_delta = value - current
            elif kind in ('item_add','item_remove'):
                row=db.execute('SELECT quantity FROM character_inventory WHERE character_id=? AND item_id=?',(cid,target)).fetchone()
                current=row['quantity'] if row else 0
                if kind=='item_remove' and current<delta:
                    raise ValueError('효과 적용 실패: 참가자 중 필요한 아이템 수량이 부족합니다.')
                definition=db.execute('SELECT max_stack FROM item_definitions WHERE item_id=?',(target,)).fetchone()
                if not definition: raise ValueError('효과 아이템이 DB에 없습니다.')
                value=current+(delta if kind=='item_add' else -delta)
                if value>definition['max_stack']: raise ValueError('효과 적용 실패: 아이템 최대 보유 수량을 초과합니다.')
                actual_delta = value - current
                name_row = db.execute('SELECT name FROM item_definitions WHERE item_id=?',(target,)).fetchone()
                item_name = name_row['name'] if name_row else target
                if value==0:
                    db.execute('DELETE FROM character_equipment WHERE character_id=? AND item_id=?',(cid,target))
                db.execute('''INSERT INTO character_inventory(character_id,item_id,quantity) VALUES (?,?,?)
                    ON CONFLICT(character_id,item_id) DO UPDATE SET quantity=excluded.quantity''',(cid,target,value))
            else: raise ValueError('지원하지 않는 효과입니다.')
            log.append({'type':kind,'target':target,'value':raw,'character':member['name'],
                        'actual_delta':actual_delta,'item_name':item_name})
    if effects:
        db.execute('INSERT INTO game_events(session_id,round_id,event_type,payload) VALUES (?,?,?,?)',
            (session['session_id'],None,'choice_effects',json.dumps({'choice_id':choice_id,'effects':log},ensure_ascii=False)))
    return log

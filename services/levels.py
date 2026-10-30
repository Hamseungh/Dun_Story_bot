"""Level thresholds, free stat allocation and administrator reset."""
from services.characters import connect
from services.sheets_loader import get_spreadsheet, read_sheet

STATS = {'체력':'bonus_hp', '정신력':'bonus_mental', '민첩':'bonus_agility', '지능':'bonus_intelligence', '힘':'bonus_strength'}


def _level_and_points(db, exp):
    rows = db.execute('SELECT level,total_exp,stat_points FROM level_rules ORDER BY level').fetchall()
    if not rows:
        return None, 0
    attained = [r for r in rows if r['total_exp'] <= exp]
    if not attained:
        return 1, 0
    return attained[-1]['level'], sum(r['stat_points'] for r in attained)


def recalculate(db, character_id, exp):
    level, _ = _level_and_points(db, exp)
    if level is not None:
        db.execute('UPDATE character_progress SET level=? WHERE character_id=?', (level, character_id))


def sync_levels():
    book = get_spreadsheet()
    records = read_sheet(book, 'Levels', ['level','total_exp','stat_points'])
    rows=[]
    for i,r in enumerate(records,2):
        if all(str(r.get(k,'')).strip()=='' for k in ('level','total_exp','stat_points')): continue
        try:
            level=int(r['level']); exp=int(r['total_exp']); points=int(r['stat_points'])
        except (ValueError,TypeError): raise ValueError(f'Levels {i}행: 숫자 형식을 확인해 주세요.')
        if level<1 or exp<0 or points<0: raise ValueError(f'Levels {i}행: 음수 또는 잘못된 레벨입니다.')
        rows.append((level,exp,points))
    if not rows or rows[0] != (1,0,0):
        raise ValueError('Levels의 첫 행은 level=1, total_exp=0, stat_points=0이어야 합니다.')
    for prev,cur in zip(rows,rows[1:]):
        if cur[0]!=prev[0]+1 or cur[1]<=prev[1]:
            raise ValueError('Levels는 레벨이 1씩 연속되고 누적 경험치가 엄격히 증가해야 합니다.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM level_rules')
        db.executemany('INSERT INTO level_rules(level,total_exp,stat_points) VALUES (?,?,?)',rows)
        chars=db.execute('SELECT character_id,exp FROM character_progress').fetchall()
        for c in chars: recalculate(db,c['character_id'],c['exp'])
        overs=db.execute("""SELECT COUNT(*) FROM character_progress p WHERE p.spent_growth_points >
            COALESCE((SELECT SUM(stat_points) FROM level_rules l WHERE l.total_exp<=p.exp),0)""").fetchone()[0]
    return len(rows),len(chars),overs


def _find(db,user_id,name):
    row=db.execute("""SELECT c.character_id,c.name,p.* FROM characters c JOIN character_progress p USING(character_id)
        WHERE c.user_id=? AND c.name=? COLLATE NOCASE""",(str(user_id),name.strip())).fetchone()
    if not row: raise ValueError('해당 사용자의 헌터를 찾을 수 없습니다.')
    return row


def status(user_id,name):
    with connect() as db:
        c=_find(db,user_id,name)
        level,earned=_level_and_points(db,c['exp'])
        spent=int(c['spent_growth_points'])
        return {'name':c['name'],'level':c['level'],'exp':c['exp'],'earned':earned,
                'spent':spent,'available':max(0,earned-spent),
                'growth':{label:int(c[col]) for label,col in STATS.items()},'configured':level is not None}


def allocate(user_id,name,stat):
    if stat not in STATS: raise ValueError('올바른 능력치를 선택해 주세요.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        c=_find(db,user_id,name)
        level,earned=_level_and_points(db,c['exp'])
        if level is None: raise ValueError('레벨 규칙이 없습니다. 운영자에게 /레벨동기화를 요청하세요.')
        if earned<=c['spent_growth_points']: raise ValueError('분배 가능한 성장 포인트가 없습니다.')
        col=STATS[stat]
        db.execute(f"""UPDATE character_progress SET {col}={col}+1,
            spent_growth_points=spent_growth_points+1 WHERE character_id=?""",(c['character_id'],))
        return {'name':c['name'],'stat':stat,'after':c[col]+1,'remaining':earned-c['spent_growth_points']-1}


def reset(user_id,name):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        c=_find(db,user_id,name)
        before={label:int(c[col]) for label,col in STATS.items()}
        db.execute("""UPDATE character_progress SET bonus_hp=0,bonus_mental=0,bonus_agility=0,
            bonus_intelligence=0,bonus_strength=0,spent_growth_points=0 WHERE character_id=?""",(c['character_id'],))
        return {'name':c['name'],'before':before,'refunded':int(c['spent_growth_points'])}

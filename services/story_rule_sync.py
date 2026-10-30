"""Import Conditions/Effects after /장면동기화; immutable per story version."""
from services.sheets_loader import get_spreadsheet, read_sheet
from services.story_engine import connect
from services.story_rules import migrate, STATS, OPS, CONDITION_TYPES, EFFECT_TYPES

CONDITION_HEADERS=['condition_id','choice_id','condition_type','target','operator','value','scope']
EFFECT_HEADERS=['effect_id','choice_id','effect_type','target','value','scope']


def _v(row,key): return str(row.get(key,'')).strip()


def sync():
    book=get_spreadsheet()
    raw_conditions=read_sheet(book,'Conditions',CONDITION_HEADERS)
    raw_effects=read_sheet(book,'Effects',EFFECT_HEADERS)
    migrate()
    with connect() as db:
        # Multiple versions of the same choice_id can coexist; use latest Stories version.
        versions={r['story_id']:r['version'] for r in db.execute('SELECT story_id,version FROM stories')}
        choices={(r['story_id'],r['choice_id']):dict(r) for r in db.execute('SELECT * FROM story_choices') if r['version']==versions.get(r['story_id'])}
        ids={}
        for (_,cid),r in choices.items():
            ids.setdefault(cid,[]).append(r)
        items={r['item_id']:dict(r) for r in db.execute('SELECT * FROM item_definitions')}
    conditions=[]; effects=[]; seen_c=set(); seen_e=set()
    for n,row in enumerate(raw_conditions,2):
        if not any(_v(row,k) for k in CONDITION_HEADERS): continue
        cid,choice,kind,target,op,value,scope=(_v(row,k) for k in CONDITION_HEADERS)
        if not cid or cid in seen_c or choice not in ids or len(ids[choice])!=1:
            raise ValueError(f'Conditions {n}행: ID 중복 또는 선택지 ID 오류/모호함')
        seen_c.add(cid)
        if kind not in CONDITION_TYPES or op not in OPS or scope not in ('any_member','session') or not target or not value:
            raise ValueError(f'Conditions {n}행: 유형/연산자/범위/값 오류')
        if kind=='flag':
            if scope!='session' or op not in ('==','!='): raise ValueError(f'Conditions {n}행: 플래그는 session ==/!= 전용')
            if value.lower() not in ('true','false'): raise ValueError(f'Conditions {n}행: 플래그 값은 true/false')
            value=value.lower()
        else:
            if scope!='any_member': raise ValueError(f'Conditions {n}행: 캐릭터 조건은 any_member')
            if kind=='stat' and target not in STATS: raise ValueError(f'Conditions {n}행: 능력치 이름 오류')
            if kind=='item' and target not in items: raise ValueError(f'Conditions {n}행: 아이템 ID 오류')
            if kind in ('grade','job') and target!=kind: raise ValueError(f'Conditions {n}행: target은 {kind}')
            if kind in ('grade','job') and op not in ('==','!='): raise ValueError(f'Conditions {n}행: 등급/직군은 ==/!= 전용')
            if kind in ('stat','item'):
                try: int(value)
                except ValueError: raise ValueError(f'Conditions {n}행: 수치 조건은 정수')
        story=ids[choice][0]
        conditions.append((story['story_id'],story['version'],cid,choice,kind,target,op,value,scope))
    for n,row in enumerate(raw_effects,2):
        if not any(_v(row,k) for k in EFFECT_HEADERS): continue
        eid,choice,kind,target,value,scope=(_v(row,k) for k in EFFECT_HEADERS)
        result=_v(row,'result') or 'always'
        if result not in ('always','success','failure'):
            raise ValueError(f'Effects {n}행: result는 always/success/failure')
        if not eid or eid in seen_e or choice not in ids or len(ids[choice])!=1:
            raise ValueError(f'Effects {n}행: ID 중복 또는 선택지 ID 오류/모호함')
        seen_e.add(eid)
        if kind not in EFFECT_TYPES or not target or not value:
            raise ValueError(f'Effects {n}행: 유형/대상/값 오류')
        if kind=='flag_set':
            if scope!='session' or value.lower() not in ('true','false'):
                raise ValueError(f'Effects {n}행: flag_set은 session 및 true/false')
            value=value.lower()
        else:
            if scope not in ('all_members','selected_character'): raise ValueError(f'Effects {n}행: 캐릭터 효과는 all_members 또는 selected_character')
            if kind in ('hp','mental','gold','exp') and target!='current':
                raise ValueError(f'Effects {n}행: 수치 효과 target은 current')
            if kind in ('item_add','item_remove') and target not in items:
                raise ValueError(f'Effects {n}행: 아이템 ID 오류')
            try: amount=int(value)
            except ValueError: raise ValueError(f'Effects {n}행: 값은 정수')
            if kind in ('item_add','item_remove') and amount<=0:
                raise ValueError(f'Effects {n}행: 아이템 수량은 양수')
            if abs(amount)>999999: raise ValueError(f'Effects {n}행: 수치 범위 초과')
        story=ids[choice][0]
        if result in ('success','failure') and story['success_rate'] is None:
            raise ValueError(f'Effects {n}행: 확률이 없는 선택지는 success/failure 효과를 사용할 수 없습니다')
        effects.append((story['story_id'],story['version'],eid,choice,kind,target,value,scope,result))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for story,ver in sorted({(r['story_id'],r['version']) for r in choices.values()}):
            old_c=[tuple(r) for r in db.execute('''SELECT story_id,version,condition_id,choice_id,condition_type,target,operator,value,scope
                FROM story_conditions WHERE story_id=? AND version=? ORDER BY condition_id''',(story,ver))]
            old_e=[tuple(r) for r in db.execute('''SELECT story_id,version,effect_id,choice_id,effect_type,target,value,scope,result
                FROM story_effects WHERE story_id=? AND version=? ORDER BY effect_id''',(story,ver))]
            new_c=sorted((x for x in conditions if x[0]==story and x[1]==ver),key=lambda x:x[2])
            new_e=sorted((x for x in effects if x[0]==story and x[1]==ver),key=lambda x:x[2])
            synced=db.execute('SELECT 1 FROM story_rulesets WHERE story_id=? AND version=?',(story,ver)).fetchone()
            if synced:
                if old_c!=new_c or old_e!=new_e:
                    raise ValueError(f'{story} v{ver}: 기존 조건/효과 변경 불가. Stories 버전을 올리고 장면동기화부터 실행하세요.')
                continue
            db.execute('INSERT INTO story_rulesets(story_id,version) VALUES (?,?)',(story,ver))
            db.executemany('''INSERT INTO story_conditions VALUES (?,?,?,?,?,?,?,?,?)''',new_c)
            db.executemany('''INSERT INTO story_effects(story_id,version,effect_id,choice_id,effect_type,target,value,scope,result) VALUES (?,?,?,?,?,?,?,?,?)''',new_e)
    return len(conditions),len(effects)

if __name__=='__main__': print('조건/효과 동기화:',sync())

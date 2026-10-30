"""Validate complete Scenes/Choices sheets, then store immutable story versions."""
from services.sheets_loader import get_spreadsheet, read_sheet
from services.story_engine import connect, migrate


def _str(row,key): return str(row.get(key,'')).strip()


def sync():
    book=get_spreadsheet()
    scenes_raw=read_sheet(book,'Scenes',['scene_id','story_id','title','content','scene_type','next_scene_id'])
    choices_raw=read_sheet(book,'Choices',['choice_id','scene_id','text','next_scene_id','display_order'])
    with connect() as db:
        stories={r['story_id']:dict(r) for r in db.execute('SELECT * FROM stories')}
    scenes={}; choices={}; positions=set()
    for index,row in enumerate(scenes_raw,2):
        sid=_str(row,'scene_id'); story=_str(row,'story_id')
        if not sid and not story and not _str(row,'title') and not _str(row,'content'): continue
        if not sid or story not in stories or not _str(row,'title') or not _str(row,'content'):
            raise ValueError(f'Scenes {index}행: 필수값 또는 등록된 story_id 확인')
        if sid in scenes: raise ValueError(f'Scenes {index}행: 중복 scene_id {sid}')
        typ=_str(row,'scene_type').lower()
        if typ not in ('normal','ending'): raise ValueError(f'Scenes {index}행: scene_type은 normal/ending')
        nxt=_str(row,'next_scene_id')
        if typ=='ending' and nxt: raise ValueError(f'Scenes {index}행: 엔딩에 다음 장면 불가')
        scenes[sid]=(story,sid,_str(row,'title'),_str(row,'content'),typ,nxt)
    for index,row in enumerate(choices_raw,2):
        cid=_str(row,'choice_id'); sid=_str(row,'scene_id')
        if not cid and not sid and not _str(row,'text'): continue
        if not cid or cid in choices or sid not in scenes or not _str(row,'text'):
            raise ValueError(f'Choices {index}행: ID 중복/필수값/scene_id 확인')
        try: order=int(row['display_order'])
        except (TypeError,ValueError): raise ValueError(f'Choices {index}행: display_order는 정수')
        if order<1 or (sid,order) in positions: raise ValueError(f'Choices {index}행: 순서 중복/범위 오류')
        positions.add((sid,order))
        nxt=_str(row,'next_scene_id')
        if nxt and (nxt not in scenes or scenes[nxt][0]!=scenes[sid][0]): raise ValueError(f'Choices {index}행: 다음 장면은 동일 스토리에 있어야 합니다')
        if scenes[sid][4]=='ending': raise ValueError(f'Choices {index}행: 엔딩에는 선택지 불가')
        rate_text=_str(row,'success_rate')
        if rate_text:
            try: rate=int(rate_text)
            except ValueError: raise ValueError(f'Choices {index}행: success_rate는 0~100 정수')
            if not 0<=rate<=100: raise ValueError(f'Choices {index}행: success_rate는 0~100 정수')
            yes=_str(row,'success_scene_id'); no=_str(row,'failure_scene_id')
            if (yes not in scenes or no not in scenes or
                scenes[yes][0]!=scenes[sid][0] or scenes[no][0]!=scenes[sid][0]):
                raise ValueError(f'Choices {index}행: 성공/실패 장면은 동일 스토리의 Scenes에 있어야 합니다')
            if nxt: raise ValueError(f'Choices {index}행: 확률 선택지는 next_scene_id를 비워 주세요')
        else:
            if not nxt: raise ValueError(f'Choices {index}행: 확률이 없는 선택지는 next_scene_id가 필요합니다')
            rate=None;yes='';no=''
            if _str(row,'success_scene_id') or _str(row,'failure_scene_id'):
                raise ValueError(f'Choices {index}행: 성공/실패 장면을 지정하려면 success_rate가 필요합니다')
        choices[cid]=(scenes[sid][0],cid,sid,_str(row,'text'),order,nxt,rate,yes,no)
    for sid,entry in scenes.items():
        story,_,_,_,typ,nxt=entry
        if nxt and (nxt not in scenes or scenes[nxt][0]!=story): raise ValueError(f'{sid}: 잘못된 next_scene_id')
        if nxt and any(c[2]==sid for c in choices.values()): raise ValueError(f'{sid}: 선택지와 next_scene_id를 동시에 지정할 수 없습니다')
        if typ=='normal' and not nxt and not any(c[2]==sid for c in choices.values()): raise ValueError(f'{sid}: 일반 장면에 다음 경로가 없습니다')
    for story_id,story in stories.items():
        if story['enabled'] and (story['start_scene_id'] not in scenes or scenes[story['start_scene_id']][0]!=story_id):
            raise ValueError(f'{story_id}: 시작 장면이 Scenes에 없습니다')
    migrate()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for story_id,story in stories.items():
            version=story['version']
            old=db.execute('SELECT 1 FROM story_scenes WHERE story_id=? AND version=? LIMIT 1',(story_id,version)).fetchone()
            if old:
                stored_scenes=[tuple(r) for r in db.execute('SELECT scene_id,title,content,scene_type,next_scene_id FROM story_scenes WHERE story_id=? AND version=? ORDER BY scene_id',(story_id,version))]
                incoming_scenes=sorted([(x[1],x[2],x[3],x[4],x[5]) for x in scenes.values() if x[0]==story_id])
                stored_choices=[tuple(r) for r in db.execute('SELECT choice_id,scene_id,text,display_order,next_scene_id,success_rate,success_scene_id,failure_scene_id FROM story_choices WHERE story_id=? AND version=? ORDER BY choice_id',(story_id,version))]
                incoming_choices=sorted([(x[1],x[2],x[3],x[4],x[5],x[6],x[7],x[8]) for x in choices.values() if x[0]==story_id])
                if stored_scenes!=incoming_scenes or stored_choices!=incoming_choices:
                    raise ValueError(f'{story_id} v{version}: 이미 저장된 버전입니다. 내용을 수정했다면 Stories의 version을 올려주세요.')
                continue
            for x in scenes.values():
                if x[0]==story_id: db.execute('INSERT INTO story_scenes(story_id,version,scene_id,title,content,scene_type,next_scene_id) VALUES (?,?,?,?,?,?,?)',(story_id,version,*x[1:]))
            for x in choices.values():
                if x[0]==story_id: db.execute('INSERT INTO story_choices(story_id,version,choice_id,scene_id,text,display_order,next_scene_id,success_rate,success_scene_id,failure_scene_id) VALUES (?,?,?,?,?,?,?,?,?,?)',(story_id,version,*x[1:]))
    return len(scenes),len(choices)

if __name__=='__main__':
    print('Scenes/Choices 동기화:',sync())

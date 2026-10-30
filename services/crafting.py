"""Standalone crafting recipes and atomic crafting operations."""
import math
import os
import random
from services.characters import connect
from services.sheets_loader import get_spreadsheet

HEADERS = ('recipe_id','recipe_type','result_item_id','result_quantity','materials','success_rate','gold_cost','enabled')
SHARD_ID = os.getenv('CRAFT_SHARD_ITEM_ID', 'scrap').strip()


def parse_materials(value):
    result = {}
    for part in value.split(','):
        part = part.strip()
        if not part:
            continue
        pieces = part.split(':')
        if len(pieces) != 2 or not pieces[0].strip():
            raise ValueError('materials는 item_id:수량,item_id:수량 형식이어야 합니다.')
        item_id = pieces[0].strip()
        try:
            quantity = int(pieces[1].strip())
        except ValueError as exc:
            raise ValueError('재료 수량은 양의 정수여야 합니다.') from exc
        if quantity < 1 or quantity > 999999 or item_id in result:
            raise ValueError('재료 수량 범위 오류 또는 중복 재료 ID')
        result[item_id] = quantity
    if not result:
        raise ValueError('필요 재료를 최소 한 개 지정해야 합니다.')
    return result


def sync_recipes():
    ws = get_spreadsheet().worksheet('Recipes')
    headers = [h.strip() for h in ws.row_values(1)]
    if len(headers) != len(set(headers)) or set(HEADERS) - set(headers):
        raise ValueError(f'Recipes 헤더 중복 또는 누락: {sorted(set(HEADERS)-set(headers))}')
    records, seen = [], set()
    with connect() as db:
        items = {r['item_id']: dict(r) for r in db.execute('SELECT * FROM item_definitions')}
    for number, values in enumerate(ws.get_all_values()[1:], 2):
        data = dict(zip(headers, values + [''] * max(0, len(headers) - len(values))))
        if not any(v.strip() for v in data.values()):
            continue
        try:
            rid, kind, result_id = (data[k].strip() for k in ('recipe_id','recipe_type','result_item_id'))
            if not rid or rid in seen or kind not in ('equipment','material'):
                raise ValueError('레시피 ID 중복/누락 또는 종류 오류')
            seen.add(rid)
            result = items.get(result_id)
            if not result or result['category'] != kind or not result['enabled']:
                raise ValueError('결과 아이템 ID가 없거나 종류가 다르거나 비활성 상태입니다.')
            qty, rate, cost = (int(data[k].strip()) for k in ('result_quantity','success_rate','gold_cost'))
            enabled = data['enabled'].strip().lower()
            if enabled not in ('1','0','true','false'):
                raise ValueError('enabled는 1/0 또는 TRUE/FALSE')
            enabled = int(enabled in ('1','true'))
            if not 1 <= qty <= result['max_stack'] or not 0 <= rate <= 100 or not 0 <= cost <= 999999999:
                raise ValueError('결과 수량/확률/제작비 범위 오류')
            if kind == 'material' and (rate != 100 or cost != 0):
                raise ValueError('재료 제작은 success_rate=100, gold_cost=0으로 입력')
            materials = parse_materials(data['materials'].strip())
            if any(mid not in items or items[mid]['category'] != 'material' or not items[mid]['enabled'] for mid in materials):
                raise ValueError('재료 ID가 없거나 material이 아니거나 비활성 상태입니다.')
            if kind == 'material' and (set(materials) != {SHARD_ID}):
                raise ValueError(f'재료 제작은 마석 조각({SHARD_ID})만 소모해야 합니다.')
            records.append((rid,kind,result_id,qty,data['materials'].strip(),rate,cost,enabled))
        except ValueError as exc:
            raise ValueError(f'Recipes {number}행: {exc}') from exc
    if any(r[1] == 'equipment' for r in records):
        shard = items.get(SHARD_ID)
        if not shard or shard['category'] != 'material' or not shard['enabled']:
            raise ValueError(f'Items 시트에 활성 마석 조각({SHARD_ID})을 material로 먼저 등록해 주세요.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM crafting_recipes')
        db.executemany('INSERT INTO crafting_recipes VALUES(?,?,?,?,?,?,?,?)',records)
    return len(records)


def _character(db, user_id, name):
    row = db.execute('''SELECT c.character_id,c.name,c.job,p.gold FROM characters c
        JOIN character_progress p ON p.character_id=c.character_id
        WHERE c.user_id=? AND c.name=? COLLATE NOCASE''',(str(user_id),name.strip())).fetchone()
    if not row:
        raise ValueError('본인 소유의 캐릭터를 찾지 못했습니다.')
    return dict(row)


def _requirements(recipe, job):
    materials = parse_materials(recipe['materials'])
    if recipe['recipe_type'] == 'equipment' and job != '제작직':
        materials = {k: math.ceil(v * 1.5) for k,v in materials.items()}
    gold = recipe['gold_cost'] if recipe['recipe_type'] == 'equipment' and job != '제작직' else 0
    return materials, gold


def _snapshot(db, character_id):
    return {r['item_id']: r['quantity'] for r in db.execute('SELECT item_id,quantity FROM character_inventory WHERE character_id=?',(character_id,))}


def _can_make(recipe, job, gold, stock):
    materials, cost = _requirements(recipe, job)
    return gold >= cost and all(stock.get(k,0) >= q for k,q in materials.items())


def list_available(user_id, name, kind):
    if kind not in ('equipment','material'):
        raise ValueError('올바르지 않은 제작 종류입니다.')
    with connect() as db:
        char = _character(db,user_id,name)
        stock = _snapshot(db,char['character_id'])
        rows = db.execute('''SELECT r.*,d.name AS result_name FROM crafting_recipes r
            JOIN item_definitions d ON d.item_id=r.result_item_id
            WHERE r.recipe_type=? AND r.enabled=1 AND d.enabled=1 ORDER BY d.name,r.recipe_id''',(kind,)).fetchall()
        available=[]
        for row in rows:
            r=dict(row)
            if _can_make(r,char['job'],char['gold'],stock):
                mats,cost = _requirements(r,char['job'])
                names={x['item_id']:x['name'] for x in db.execute('SELECT item_id,name FROM item_definitions')}
                r['required'] = [(mid,names.get(mid,mid),qty) for mid,qty in mats.items()]
                r['cost'] = cost
                available.append(r)
        return char,available


def craft(user_id, name, recipe_id):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        char = _character(db,user_id,name)
        recipe_row = db.execute('''SELECT r.*,d.name AS result_name,d.max_stack AS result_max,d.enabled AS result_enabled
            FROM crafting_recipes r JOIN item_definitions d ON d.item_id=r.result_item_id
            WHERE r.recipe_id=? AND r.enabled=1''',(recipe_id,)).fetchone()
        if not recipe_row or not recipe_row['result_enabled']:
            raise ValueError('현재 제작할 수 없는 레시피입니다.')
        r=dict(recipe_row)
        mats,cost = _requirements(r,char['job'])
        stock=_snapshot(db,char['character_id'])
        if not _can_make(r,char['job'],char['gold'],stock):
            raise ValueError('재료 또는 소지금이 부족합니다. 제작 목록을 다시 확인해 주세요.')
        success = r['recipe_type'] == 'material' or random.randrange(100) < r['success_rate']
        gained_id = r['result_item_id'] if success else SHARD_ID
        gained_qty = r['result_quantity'] if success else sum(mats.values())//2
        if gained_qty:
            item = db.execute('SELECT name,max_stack,enabled FROM item_definitions WHERE item_id=?',(gained_id,)).fetchone()
            if not item or not item['enabled']:
                raise ValueError('결과 아이템 또는 마석 조각이 등록되어 있지 않습니다.')
            new_qty=stock.get(gained_id,0)-mats.get(gained_id,0)+gained_qty
            if new_qty > item['max_stack']:
                raise ValueError(f'{item["name"]} 최대 보유 수량을 초과합니다.')
        else:
            item=None
        for mid,qty in mats.items():
            db.execute('UPDATE character_inventory SET quantity=quantity-? WHERE character_id=? AND item_id=?',(qty,char['character_id'],mid))
        if cost:
            db.execute('UPDATE character_progress SET gold=gold-? WHERE character_id=?',(cost,char['character_id']))
        if gained_qty:
            db.execute('''INSERT INTO character_inventory(character_id,item_id,quantity) VALUES(?,?,?)
                ON CONFLICT(character_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity''',(char['character_id'],gained_id,gained_qty))
        return {'success':success,'name':r['result_name'],'gained':item['name'] if item else None,
                'quantity':gained_qty,'materials':mats,'cost':cost}

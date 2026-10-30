"""Validate the entire Items worksheet before any database changes."""
from services.sheets_loader import get_spreadsheet
from services.items import connect, migrate, SLOTS

HEADERS = ('item_id','name','category','slot','description','hp_stat','mental_stat','agility','intelligence','strength','max_stack','enabled','sell_price')
CATEGORIES = {'material','consumable','equipment'}


def fetch_items():
    ws = get_spreadsheet().worksheet('Items')
    headers = [h.strip() for h in ws.row_values(1)]
    if len(headers) != len(set(headers)) or set(HEADERS) - set(headers):
        raise ValueError(f'Items 헤더 중복 또는 누락: {sorted(set(HEADERS)-set(headers))}')
    items, seen = [], set()
    for num, values in enumerate(ws.get_all_values()[1:],2):
        data = dict(zip(headers, values + ['']*max(0,len(headers)-len(values))))
        if not any(v.strip() for v in data.values()):
            continue
        item_id, name, category, slot = [data[k].strip() for k in ('item_id','name','category','slot')]
        if not item_id or not name or len(name)>80 or item_id in seen:
            raise ValueError(f'Items {num}행: ID/이름 누락, 이름 길이 또는 중복 ID')
        seen.add(item_id)
        if category not in CATEGORIES:
            raise ValueError(f'Items {num}행: category는 material/consumable/equipment')
        if (category=='equipment' and slot not in SLOTS) or (category!='equipment' and slot):
            raise ValueError(f'Items {num}행: 장비 슬롯은 weapon/armor/accessory, 비장비는 공란')
        try:
            stats = [int(data[k].strip()) for k in ('hp_stat','mental_stat','agility','intelligence','strength')]
            max_stack = int(data['max_stack'].strip())
            enabled = int(data['enabled'].strip())
            sell_price = int(data['sell_price'].strip()) if data['sell_price'].strip() else None
        except ValueError as exc:
            raise ValueError(f'Items {num}행: 정수 컬럼 확인') from exc
        if sell_price is not None and sell_price < 0:
            raise ValueError(f'Items {num}행: sell_price는 0 이상의 정수 또는 공란이어야 합니다.')
        if any(abs(v)>999 for v in stats) or not 1<=max_stack<=999999 or enabled not in (0,1):
            raise ValueError(f'Items {num}행: 능력치/최대수량/enabled 범위 오류')
        if category!='equipment' and any(stats):
            raise ValueError(f'Items {num}행: 장비 외 아이템의 능력치 보너스는 0으로 입력')
        items.append((item_id,name,category,slot,data['description'].strip(),*stats,max_stack,enabled,sell_price))
    return items


def sync_items():
    records = fetch_items()
    migrate()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        # Don't allow an equipped item to change category/slot on resync.
        for r in records:
            equipped = db.execute('''SELECT 1 FROM character_equipment WHERE item_id=? LIMIT 1''', (r[0],)).fetchone()
            if equipped:
                old = db.execute('SELECT category,slot FROM item_definitions WHERE item_id=?', (r[0],)).fetchone()
                if old and (old['category'] != r[2] or old['slot'] != r[3]):
                    raise ValueError(f'{r[0]}: 장착 중인 장비의 category/slot 변경은 허용되지 않습니다.')
        db.executemany('''INSERT INTO item_definitions
            (item_id,name,category,slot,description,hp_stat,mental_stat,agility,intelligence,strength,max_stack,enabled,sell_price)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(item_id) DO UPDATE SET name=excluded.name,category=excluded.category,
            slot=excluded.slot,description=excluded.description,hp_stat=excluded.hp_stat,
            mental_stat=excluded.mental_stat,agility=excluded.agility,intelligence=excluded.intelligence,
            strength=excluded.strength,max_stack=excluded.max_stack,enabled=excluded.enabled,sell_price=excluded.sell_price''', records)
    return len(records)

if __name__ == '__main__':
    print(f'아이템 {sync_items()}개 동기화 완료')

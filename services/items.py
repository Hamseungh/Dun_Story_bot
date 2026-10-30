"""Persistent, character-owned items and equipment. No inventory reset on sync."""
from services.characters import connect

SLOTS = {'weapon': '무기', 'armor': '방어구', 'accessory': '장신구'}
STATS = ('hp_stat', 'mental_stat', 'agility', 'intelligence', 'strength')


def migrate():
    from database import init_db
    init_db()


def owned(user_id, name):
    row = None
    with connect() as db:
        row = db.execute('SELECT character_id,name FROM characters WHERE user_id=? AND name=? COLLATE NOCASE', (str(user_id), name.strip())).fetchone()
    if row is None:
        raise ValueError('본인 소유의 캐릭터를 찾지 못했습니다.')
    return dict(row)


def inventory(user_id, name):
    c = owned(user_id, name)
    with connect() as db:
        rows = db.execute('''SELECT i.item_id,i.quantity,d.name,d.category,d.slot,d.description,
            e.slot AS equipped_slot FROM character_inventory i
            LEFT JOIN item_definitions d ON d.item_id=i.item_id
            LEFT JOIN character_equipment e ON e.character_id=i.character_id AND e.item_id=i.item_id
            WHERE i.character_id=? AND i.quantity>0 ORDER BY d.category,d.name,i.item_id''', (c['character_id'],)).fetchall()
    return c, [dict(r) for r in rows]


def equipment(user_id, name):
    c = owned(user_id, name)
    with connect() as db:
        rows = db.execute('''SELECT e.slot,e.item_id,d.name,d.hp_stat,d.mental_stat,
            d.agility,d.intelligence,d.strength FROM character_equipment e
            LEFT JOIN item_definitions d ON d.item_id=e.item_id
            WHERE e.character_id=? ORDER BY e.slot''', (c['character_id'],)).fetchall()
    return c, [dict(r) for r in rows]


def item_information(item_name):
    """Look up a synced item definition by display name, regardless of ownership."""
    name = item_name.strip()
    if not name:
        raise ValueError('아이템 이름을 입력해 주세요.')
    with connect() as connection:
        matches = connection.execute(
            'SELECT * FROM item_definitions WHERE name=? COLLATE NOCASE ORDER BY item_id',
            (name,)
        ).fetchall()
    if not matches:
        raise ValueError('해당 이름의 아이템을 찾을 수 없습니다. /아이템동기화 상태를 확인해 주세요.')
    if len(matches) > 1:
        raise ValueError('같은 이름의 아이템이 여러 개 등록되어 있습니다. 관리자에게 아이템 이름 구분을 요청해 주세요.')
    return dict(matches[0])


def grant(user_id, name, item_id, quantity):
    if not 1 <= quantity <= 999999:
        raise ValueError('지급 수량은 1~999999 사이여야 합니다.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        c = db.execute('SELECT character_id FROM characters WHERE user_id=? AND name=? COLLATE NOCASE', (str(user_id),name.strip())).fetchone()
        if not c:
            raise ValueError('해당 사용자 소유의 캐릭터가 없습니다.')
        item = db.execute('SELECT * FROM item_definitions WHERE item_id=? AND enabled=1', (item_id,)).fetchone()
        if not item:
            raise ValueError('등록된 활성 아이템 ID가 아닙니다.')
        existing = db.execute('SELECT quantity FROM character_inventory WHERE character_id=? AND item_id=?', (c['character_id'],item_id)).fetchone()
        current = existing['quantity'] if existing else 0
        if current + quantity > item['max_stack']:
            raise ValueError(f"최대 보유 수량은 {item['max_stack']}개입니다. 현재 {current}개")
        db.execute('''INSERT INTO character_inventory(character_id,item_id,quantity) VALUES(?,?,?)
            ON CONFLICT(character_id,item_id) DO UPDATE SET quantity=excluded.quantity''',
            (c['character_id'],item_id,current+quantity))
        return item['name'], current+quantity


def equip(user_id, name, item_name):
    c = owned(user_id, name)
    item_name = item_name.strip()
    if not item_name:
        raise ValueError('장착할 장비 이름을 입력해 주세요.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        matches = db.execute('''SELECT d.* FROM character_inventory i
            JOIN item_definitions d ON d.item_id=i.item_id
            WHERE i.character_id=? AND d.name=? COLLATE NOCASE AND i.quantity>0''',
            (c['character_id'], item_name)).fetchall()
        if not matches:
            raise ValueError('해당 이름의 소유 아이템이 없습니다.')
        if len(matches) > 1:
            raise ValueError('동일한 이름의 아이템이 여러 종류입니다. 운영자에게 이름 구분을 요청해 주세요.')
        item = matches[0]
        if item['category'] != 'equipment' or item['slot'] not in SLOTS:
            raise ValueError('장착 가능한 장비가 아닙니다.')
        if not item['enabled']:
            raise ValueError('현재 비활성화된 장비입니다.')
        item_id = item['item_id']
        db.execute('DELETE FROM character_equipment WHERE character_id=? AND item_id=?', (c['character_id'],item_id))
        db.execute('''INSERT INTO character_equipment(character_id,slot,item_id) VALUES(?,?,?)
            ON CONFLICT(character_id,slot) DO UPDATE SET item_id=excluded.item_id''',
            (c['character_id'],item['slot'],item_id))
        return item['name'], SLOTS[item['slot']]


def unequip(user_id, name, slot):
    if slot not in SLOTS:
        raise ValueError('올바르지 않은 장비 슬롯입니다.')
    c = owned(user_id,name)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('''SELECT e.item_id, d.name FROM character_equipment e
            LEFT JOIN item_definitions d ON d.item_id=e.item_id
            WHERE e.character_id=? AND e.slot=?''', (c['character_id'],slot)).fetchone()
        if not row:
            raise ValueError('해당 슬롯에 장착된 장비가 없습니다.')
        db.execute('DELETE FROM character_equipment WHERE character_id=? AND slot=?', (c['character_id'],slot))
        return row['name'] or '이름 없는 아이템'

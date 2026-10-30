"""Consumable effects: sheet-defined, atomic character-owned use."""
from services.characters import connect

RESOURCE_LABELS = {'hp': '체력', 'mental': '정신력'}


def migrate():
    from database import init_db
    init_db()


def use_item(user_id, character_name, item_name):
    """Consume exactly one item only when a positive recovery can be applied."""
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        char = db.execute('''SELECT c.character_id,c.name,c.hp_stat,c.mental_stat,
            p.current_hp,p.current_mental,p.bonus_hp,p.bonus_mental
            FROM characters c JOIN character_progress p USING(character_id)
            WHERE c.user_id=? AND c.name=? COLLATE NOCASE''',
            (str(user_id), character_name.strip())).fetchone()
        if char is None:
            raise ValueError('본인 소유의 캐릭터를 찾지 못했습니다.')
        matches = db.execute('''SELECT d.item_id,d.name,d.category,d.enabled,i.quantity,
            ce.target,ce.amount FROM character_inventory i
            JOIN item_definitions d ON d.item_id=i.item_id
            LEFT JOIN consumable_effects ce ON ce.item_id=d.item_id
            WHERE i.character_id=? AND d.name=? COLLATE NOCASE AND i.quantity>0''',
            (char['character_id'], item_name.strip())).fetchall()
        if not matches:
            raise ValueError('해당 이름의 소유 아이템이 없습니다.')
        if len(matches) != 1:
            raise ValueError('동일한 이름의 아이템이 여러 종류입니다. 운영자에게 이름 구분을 요청해 주세요.')
        item = matches[0]
        if item['category'] != 'consumable' or not item['enabled']:
            raise ValueError('사용 가능한 소비아이템이 아닙니다.')
        if item['target'] is None:
            raise ValueError('이 소비아이템에는 사용 효과가 등록되지 않았습니다.')
        target = item['target']
        stat = 'hp_stat' if target == 'hp' else 'mental_stat'
        growth = 'bonus_hp' if target == 'hp' else 'bonus_mental'
        current_col = 'current_hp' if target == 'hp' else 'current_mental'
        multiplier = 20 if target == 'hp' else 10
        equipment_bonus = db.execute(f'''SELECT COALESCE(SUM(d.{stat}),0) AS bonus
            FROM character_equipment e
            JOIN character_inventory i ON i.character_id=e.character_id
                AND i.item_id=e.item_id AND i.quantity>0
            JOIN item_definitions d ON d.item_id=e.item_id
                AND d.category='equipment' AND d.slot=e.slot
            WHERE e.character_id=?''', (char['character_id'],)).fetchone()['bonus']
        maximum = max(0, (int(char[stat]) + int(char[growth])) * multiplier + int(equipment_bonus))
        before = int(char[current_col])
        after = min(maximum, before + int(item['amount']))
        if after <= before:
            raise ValueError(f'{RESOURCE_LABELS[target]}이 이미 최대치이거나 회복할 수 없는 상태입니다. 아이템은 소모되지 않았습니다.')
        db.execute(f'UPDATE character_progress SET {current_col}=? WHERE character_id=?',
                   (after, char['character_id']))
        db.execute('''UPDATE character_inventory SET quantity=quantity-1
            WHERE character_id=? AND item_id=? AND quantity>0''',
            (char['character_id'], item['item_id']))
        return {'character': char['name'], 'item': item['name'],
                'resource': RESOURCE_LABELS[target], 'recovered': after-before,
                'current': after, 'maximum': maximum, 'remaining': int(item['quantity'])-1}

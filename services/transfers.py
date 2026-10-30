"""Atomic player-to-player transfers, using the existing character SQLite schema."""
from services.characters import connect


def _character(db, user_id, name, label):
    if not name or not name.strip():
        raise ValueError(f'{label} 캐릭터 이름을 입력해 주세요.')
    row = db.execute('SELECT character_id, name FROM characters WHERE user_id=? AND name=? COLLATE NOCASE', (str(user_id), name.strip())).fetchone()
    if row is None:
        raise ValueError(f'{label} 캐릭터를 찾지 못했습니다. 소유자와 이름을 확인해 주세요.')
    return row


def _participants(db, sender_id, sender_name, recipient_id, recipient_name):
    sender = _character(db, sender_id, sender_name, '보내는')
    recipient = _character(db, recipient_id, recipient_name, '받는')
    if sender['character_id'] == recipient['character_id']:
        raise ValueError('동일한 캐릭터에게 양도할 수 없습니다.')
    return sender, recipient


def transfer_gold(sender_id, sender_name, recipient_id, recipient_name, amount):
    if not isinstance(amount, int) or not 1 <= amount <= 1_000_000_000:
        raise ValueError('양도 금액은 1~1,000,000,000 골드여야 합니다.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        sender, recipient = _participants(db, sender_id, sender_name, recipient_id, recipient_name)
        src = db.execute('SELECT gold FROM character_progress WHERE character_id=?', (sender['character_id'],)).fetchone()
        dst = db.execute('SELECT gold FROM character_progress WHERE character_id=?', (recipient['character_id'],)).fetchone()
        if not src or not dst:
            raise ValueError('캐릭터 성장 정보가 없습니다.')
        if src['gold'] < amount:
            raise ValueError(f'골드가 부족합니다. 현재 보유: {src["gold"]:,} 골드')
        db.execute('UPDATE character_progress SET gold=gold-? WHERE character_id=?', (amount, sender['character_id']))
        db.execute('UPDATE character_progress SET gold=gold+? WHERE character_id=?', (amount, recipient['character_id']))
        return sender['name'], recipient['name'], amount


def transfer_item(sender_id, sender_name, recipient_id, recipient_name, item_name, quantity):
    if not isinstance(quantity, int) or not 1 <= quantity <= 999_999:
        raise ValueError('양도 수량은 1~999,999개여야 합니다.')
    if not item_name or not item_name.strip():
        raise ValueError('아이템 이름을 입력해 주세요.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        sender, recipient = _participants(db, sender_id, sender_name, recipient_id, recipient_name)
        matches = db.execute('''SELECT i.item_id, i.quantity, d.name, d.max_stack
            FROM character_inventory i JOIN item_definitions d ON d.item_id=i.item_id
            WHERE i.character_id=? AND d.name=? COLLATE NOCASE AND i.quantity>0''',
            (sender['character_id'], item_name.strip())).fetchall()
        if not matches:
            raise ValueError('보내는 캐릭터가 해당 아이템을 보유하고 있지 않습니다.')
        if len(matches) > 1:
            raise ValueError('같은 이름의 아이템이 여러 종류입니다. 운영자에게 이름 구분을 요청해 주세요.')
        item = matches[0]
        equipped = db.execute('SELECT 1 FROM character_equipment WHERE character_id=? AND item_id=?',
                              (sender['character_id'], item['item_id'])).fetchone()
        transferable = item['quantity'] - (1 if equipped else 0)
        if transferable < quantity:
            raise ValueError(f'양도 가능한 수량이 부족합니다. 장착 중인 장비 1개는 제외됩니다. 양도 가능: {transferable}개')
        dst = db.execute('SELECT quantity FROM character_inventory WHERE character_id=? AND item_id=?',
                         (recipient['character_id'], item['item_id'])).fetchone()
        current = dst['quantity'] if dst else 0
        if current + quantity > item['max_stack']:
            raise ValueError(f'받는 캐릭터의 최대 보유 수량({item["max_stack"]}개)을 초과합니다. 현재 {current}개')
        db.execute('UPDATE character_inventory SET quantity=quantity-? WHERE character_id=? AND item_id=?',
                   (quantity, sender['character_id'], item['item_id']))
        # Keep zero-quantity rows if they are referenced by equipment (they cannot be if transferred entirely).
        db.execute('DELETE FROM character_inventory WHERE character_id=? AND item_id=? AND quantity=0',
                   (sender['character_id'], item['item_id']))
        db.execute('''INSERT INTO character_inventory(character_id,item_id,quantity) VALUES(?,?,?)
            ON CONFLICT(character_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity''',
            (recipient['character_id'], item['item_id'], quantity))
        return sender['name'], recipient['name'], item['name'], quantity

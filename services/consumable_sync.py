"""Import ConsumableEffects tab without modifying the Items sync process."""
from services.sheets_loader import get_spreadsheet
from services.characters import connect
from services.consumables import migrate

HEADERS = ('item_id', 'target', 'amount')


def fetch_effects():
    ws = get_spreadsheet().worksheet('ConsumableEffects')
    headers = [h.strip() for h in ws.row_values(1)]
    if len(headers) != len(set(headers)) or any(h not in headers for h in HEADERS):
        raise ValueError('ConsumableEffects 헤더를 확인하세요: item_id, target, amount')
    records, seen = [], set()
    for row_num, values in enumerate(ws.get_all_values()[1:], 2):
        data = dict(zip(headers, values + [''] * max(0, len(headers)-len(values))))
        if not any(str(v).strip() for v in data.values()):
            continue
        item_id = data['item_id'].strip()
        target = data['target'].strip().lower()
        try:
            amount = int(data['amount'].strip())
        except ValueError as exc:
            raise ValueError(f'ConsumableEffects {row_num}행: amount는 양의 정수여야 합니다.') from exc
        if not item_id or item_id in seen or target not in ('hp', 'mental') or not 1 <= amount <= 999999:
            raise ValueError(f'ConsumableEffects {row_num}행: ID 중복/누락, target 또는 amount 오류')
        seen.add(item_id)
        records.append((item_id, target, amount))
    return records


def sync_effects():
    records = fetch_effects()
    migrate()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for item_id, _, _ in records:
            item = db.execute('SELECT category FROM item_definitions WHERE item_id=?', (item_id,)).fetchone()
            if item is None or item['category'] != 'consumable':
                raise ValueError(f'{item_id}: Items 탭에 동기화된 소비아이템이 아닙니다.')
        db.executemany('''INSERT INTO consumable_effects(item_id,target,amount) VALUES(?,?,?)
            ON CONFLICT(item_id) DO UPDATE SET target=excluded.target,amount=excluded.amount''', records)
        if records:
            db.execute('DELETE FROM consumable_effects WHERE item_id NOT IN (' + ','.join('?' for _ in records) + ')', [r[0] for r in records])
        else:
            db.execute('DELETE FROM consumable_effects')
    return len(records)


if __name__ == '__main__':
    print(f'소비아이템 효과 {sync_effects()}개 동기화 완료')

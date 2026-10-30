"""Administrator testing controls for current HP/mental, with equipment-aware caps."""
from services.characters import connect

COLUMNS = {'hp': ('current_hp', 'hp_stat', 'bonus_hp', 20),
           'mental': ('current_mental', 'mental_stat', 'bonus_mental', 10)}

def adjust_resource(user_id, character_name, target, value, *, relative=False):
    if target not in COLUMNS:
        raise ValueError('대상은 hp 또는 mental이어야 합니다.')
    col, stat, bonus, factor = COLUMNS[target]
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute(f"""SELECT c.character_id,c.name,c.{stat} AS base,
            p.{bonus} AS growth,p.{col} AS current
            FROM characters c JOIN character_progress p USING(character_id)
            WHERE c.user_id=? AND c.name=? COLLATE NOCASE""",
            (str(user_id), character_name.strip())).fetchone()
        if row is None:
            raise ValueError('해당 사용자의 캐릭터를 찾지 못했습니다.')
        eq = db.execute(f"""SELECT COALESCE(SUM(d.{stat}),0) AS bonus
            FROM character_equipment e
            JOIN character_inventory i ON i.character_id=e.character_id
                AND i.item_id=e.item_id AND i.quantity>0
            JOIN item_definitions d ON d.item_id=e.item_id AND d.slot=e.slot
                AND d.category='equipment'
            WHERE e.character_id=?""", (row['character_id'],)).fetchone()['bonus']
        maximum = max(0, (int(row['base']) + int(row['growth'])) * factor + int(eq))
        requested = int(row['current']) + int(value) if relative else int(value)
        after = min(max(0,requested),maximum)
        db.execute(f'UPDATE character_progress SET {col}=? WHERE character_id=?',
                   (after,row['character_id']))
        return {'name':row['name'],'target':target,'before':int(row['current']),
                'after':after,'maximum':maximum,'clamped':after!=requested}


def set_character_value(user_id, character_name, target, value):
    """절대값 설정. 경험치 변경 시 레벨 규칙을 즉시 재적용한다."""
    labels = {'hp': '체력', 'mental': '정신력', 'gold': '골드', 'exp': '경험치'}
    if target not in labels:
        raise ValueError('설정 가능한 수치는 체력, 정신력, 골드, 경험치입니다.')
    if int(value) < 0:
        raise ValueError('설정값은 0 이상이어야 합니다.')
    if target in ('hp', 'mental'):
        result = adjust_resource(user_id, character_name, target, value, relative=False)
        result.update(label=labels[target], level_before=None, level_after=None)
        return result
    from services.levels import recalculate
    column = 'gold' if target == 'gold' else 'exp'
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("""SELECT c.character_id,c.name,p.gold,p.exp,p.level
            FROM characters c JOIN character_progress p USING(character_id)
            WHERE c.user_id=? AND c.name=? COLLATE NOCASE""",
            (str(user_id), character_name.strip())).fetchone()
        if row is None:
            raise ValueError('해당 사용자의 캐릭터를 찾지 못했습니다.')
        before = int(row[column])
        db.execute(f'UPDATE character_progress SET {column}=? WHERE character_id=?',
                   (int(value), row['character_id']))
        if target == 'exp':
            recalculate(db, row['character_id'], int(value))
        level_after = db.execute('SELECT level FROM character_progress WHERE character_id=?',
                                 (row['character_id'],)).fetchone()['level']
        return {'name': row['name'], 'label': labels[target], 'before': before,
                'after': int(value), 'maximum': None, 'clamped': False,
                'level_before': int(row['level']), 'level_after': int(level_after)}

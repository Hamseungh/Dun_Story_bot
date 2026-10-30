"""Character sheet + persistent growth + equipped item bonuses (read-only)."""
from services.characters import connect, find_character

STAT_LABELS = {'hp_stat': '체력', 'mental_stat': '정신력', 'agility': '민첩', 'intelligence': '지능', 'strength': '힘'}
GROWTH_KEYS = {'hp_stat': 'bonus_hp', 'mental_stat': 'bonus_mental', 'agility': 'bonus_agility', 'intelligence': 'bonus_intelligence', 'strength': 'bonus_strength'}


def get_character_stats(user_id, name):
    """Returns (character, stats). stats[stat] has base/growth/equipment/total.

    Equipment is joined through the owned inventory; stale equipment entries are ignored.
    HP and mental stat are points; their maximum resource values use x20/x10.
    """
    character = find_character(user_id, name)
    if character is None:
        return None, None
    bonuses = {stat: 0 for stat in STAT_LABELS}
    with connect() as conn:
        rows = conn.execute('''
            SELECT d.hp_stat, d.mental_stat, d.agility, d.intelligence, d.strength
            FROM character_equipment e
            JOIN character_inventory i ON i.character_id=e.character_id
                AND i.item_id=e.item_id AND i.quantity>0
            JOIN item_definitions d ON d.item_id=e.item_id
                AND d.category='equipment' AND d.slot=e.slot
            WHERE e.character_id=?
        ''', (character['character_id'],)).fetchall()
    for row in rows:
        for stat in STAT_LABELS:
            bonuses[stat] += int(row[stat] or 0)
    stats = {}
    for stat in STAT_LABELS:
        base = int(character[stat] or 0)
        growth = int(character[GROWTH_KEYS[stat]] or 0)
        equipment = bonuses[stat]
        stats[stat] = {'base': base, 'growth': growth, 'equipment': equipment,
                       'total': base + growth + equipment}
    return character, stats

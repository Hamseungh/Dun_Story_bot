"""Player-owned characters; persistent progression shared by games."""
import sqlite3
from database import DB_PATH


def connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def migrate():
    """Compatibility entry point; schema is centrally created in database.init_db."""
    from database import init_db
    init_db()


def list_characters(user_id):
    with connect() as conn:
        return [dict(r) for r in conn.execute('''
            SELECT c.*,p.level,p.exp,p.gold,p.current_hp,p.current_mental,
                   p.bonus_hp,p.bonus_mental,p.bonus_agility,
                   p.bonus_intelligence,p.bonus_strength
            FROM characters c JOIN character_progress p USING(character_id)
            WHERE c.user_id=? ORDER BY c.character_id
        ''', (str(user_id),))]


def find_character(user_id, name):
    for c in list_characters(user_id):
        if c['name'].casefold() == name.strip().casefold():
            return c
    return None


def get_members(session_id):
    with connect() as conn:
        return [dict(r) for r in conn.execute('''
            SELECT sm.user_id,sm.role,c.character_id,c.name AS character_name
            FROM session_members sm LEFT JOIN characters c ON c.character_id=sm.character_id
            WHERE sm.session_id=? AND sm.active=1 ORDER BY sm.joined_at
        ''', (session_id,))]


def join_with_character(session_id, user_id, name):
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        character = conn.execute('''SELECT * FROM characters
            WHERE user_id=? AND name=? COLLATE NOCASE''', (str(user_id), name.strip())).fetchone()
        if not character:
            raise ValueError('등록된 내 캐릭터를 찾지 못했습니다. /헌터목록으로 확인하세요.')
        # 참가 시점의 현재 체력/정신력을 확인한다. 초기 능력치가 아닌 진행 데이터 기준.
        progress = conn.execute('''SELECT current_hp, current_mental
            FROM character_progress WHERE character_id=?''',
            (character['character_id'],)).fetchone()
        if not progress:
            raise ValueError('헌터의 진행 데이터를 찾을 수 없습니다. 관리자에게 문의해 주세요.')
        if progress['current_hp'] is None or progress['current_hp'] <= 0:
            raise ValueError('현재 체력이 0인 헌터는 참가할 수 없습니다. 체력을 회복한 후 다시 시도해 주세요.')
        if progress['current_mental'] is None or progress['current_mental'] <= 0:
            raise ValueError('현재 정신력이 0인 헌터는 참가할 수 없습니다. 정신력을 회복한 후 다시 시도해 주세요.')
        session = conn.execute('SELECT status FROM game_sessions WHERE session_id=?', (session_id,)).fetchone()
        if not session or session['status'] != 'waiting':
            raise ValueError('현재 참가할 수 없는 게임입니다.')
        member = conn.execute('''SELECT character_id,active FROM session_members
            WHERE session_id=? AND user_id=?''', (session_id, str(user_id))).fetchone()
        if member and member['active'] and member['character_id'] is not None:
            raise ValueError('이미 헌터를 선택해 참가했습니다.')
        if member:
            conn.execute('''UPDATE session_members SET character_id=?,active=1
                WHERE session_id=? AND user_id=?''', (character['character_id'], session_id, str(user_id)))
        else:
            conn.execute('''INSERT INTO session_members(session_id,user_id,character_id)
                VALUES (?,?,?)''', (session_id, str(user_id), character['character_id']))
        return dict(character)


GRADE_LIMITS = {'F': 8, 'E': 10, 'D': 12, 'C': 15, 'B': 17, 'A': 19, 'S': 22}
JOBS = {'사무직', '현장직', '제작직', '연구직', '간부직'}


def register_character(user_id, name, job, grade, hp, mental, agility, intelligence, strength):
    # Register a new character without Sheets; preserve legacy schema.
    import uuid
    name = name.strip()
    grade = grade.strip().upper()
    if not 1 <= len(name) <= 40:
        raise ValueError('헌터 이름은 1~40자로 입력해 주세요.')
    if grade not in GRADE_LIMITS:
        raise ValueError('등록 가능한 등급은 F~S입니다.')
    if job not in JOBS:
        raise ValueError('직군은 사무직, 현장직, 제작직, 연구직, 간부직 중 하나여야 합니다.')
    stats = (hp, mental, agility, intelligence, strength)
    if any(type(v) is not int or not 1 <= v <= 5 for v in stats):
        raise ValueError('각 능력치는 1~5 사이의 정수여야 합니다.')
    total = sum(stats)
    if total > GRADE_LIMITS[grade]:
        raise ValueError(f'{grade}등급의 초기 능력치 합계는 {GRADE_LIMITS[grade]} 이하여야 합니다. (현재 {total})')
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT 1 FROM characters WHERE user_id=? AND name=? COLLATE NOCASE',
                        (str(user_id), name)).fetchone():
            raise ValueError('이미 같은 이름으로 등록한 헌터가 있습니다.')
        # Retain legacy NOT NULL UNIQUE sheet_character_id column for compatibility.
        local_id = 'discord_' + uuid.uuid4().hex
        cur = conn.execute('''INSERT INTO characters
            (sheet_character_id,user_id,name,grade,job,hp_stat,mental_stat,agility,intelligence,strength)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (local_id, str(user_id), name, grade, job, *stats))
        conn.execute('''INSERT INTO character_progress(character_id,current_hp,current_mental)
            VALUES (?,?,?)''', (cur.lastrowid, hp * 20, mental * 10))
    return {'name': name, 'grade': grade, 'job': job, 'stats': stats, 'total': total,
            'limit': GRADE_LIMITS[grade]}

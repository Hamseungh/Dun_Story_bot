"""Persistent reports, source links, and approval routing."""
from services.characters import connect, find_character


def _character(user_id, name):
    c = find_character(user_id, name)
    if not c:
        raise ValueError('본인이 소유한 캐릭터를 찾을 수 없습니다.')
    return c


def _validate(title, content):
    title, content = title.strip(), content.strip()
    if not 1 <= len(title) <= 100 or not 1 <= len(content) <= 3000:
        raise ValueError('보고서 이름은 1~100자, 내용은 1~3000자로 작성해 주세요.')
    return title, content


def prepare(user_id, name, title, content):
    c = _character(user_id, name)
    title, content = _validate(title, content)
    if c['job'] != '사무직':
        return c, []
    with connect() as db:
        rows = db.execute("""SELECT * FROM reports
            WHERE status='pending_office' AND report_type='field' AND report_id NOT IN (SELECT report_id FROM processed_report_ids)
               OR status='returned' AND report_type='official' AND report_id NOT IN (SELECT report_id FROM processed_report_ids)
            ORDER BY report_id DESC""").fetchall()
    if not rows:
        raise ValueError('참고할 미처리 현장 보고 또는 반려된 사무 보고가 없습니다.')
    return c, [dict(r) for r in rows]


def submit(user_id, name, title, content, source_ids=None):
    c = _character(user_id, name)
    title, content = _validate(title, content)
    routes = {'현장직': ('field', 'pending_office'), '간부직': ('field', 'pending_office'),
              '사무직': ('official', 'pending_executive'), '연구직': ('research', 'pending_executive')}
    if c['job'] not in routes:
        raise ValueError('이 직군은 보고서를 작성할 수 없습니다.')
    kind, status = routes[c['job']]
    ids = list(dict.fromkeys(int(x) for x in (source_ids or [])))
    if kind == 'official' and not ids:
        raise ValueError('사무직은 참고할 보고서를 한 건 이상 선택해야 합니다.')
    if kind != 'official' and ids:
        raise ValueError('이 직군은 참고 보고서를 선택할 수 없습니다.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if ids:
            placeholders = ','.join('?' for _ in ids)
            sources = db.execute(f'SELECT * FROM reports WHERE report_id IN ({placeholders})', ids).fetchall()
            processed = {row[0] for row in db.execute(f'SELECT report_id FROM processed_report_ids WHERE report_id IN ({placeholders})', ids)}
            if processed or len(sources) != len(ids) or any(not (
                (r['report_type'] == 'field' and r['status'] == 'pending_office') or
                (r['report_type'] == 'official' and r['status'] == 'returned')
            ) for r in sources):
                raise ValueError('참고 보고서가 이미 처리되었거나 선택할 수 없습니다. 다시 확인해 주세요.')
        cur = db.execute('''INSERT INTO reports(author_character_id,author_name,author_job,title,content,report_type,status)
            VALUES (?,?,?,?,?,?,?)''', (c['character_id'], c['name'], c['job'], title, content, kind, status))
        new_id = cur.lastrowid
        if ids:
            # Direct sources, plus inherited field sources from returned official reports.
            all_ids = set(ids)
            placeholders = ','.join('?' for _ in ids)
            inherited = db.execute(f'''SELECT DISTINCT rl.source_report_id FROM report_links rl
                JOIN reports r ON r.report_id=rl.source_report_id
                WHERE rl.report_id IN ({placeholders}) AND r.report_type='field' ''', ids).fetchall()
            all_ids.update(r['source_report_id'] for r in inherited)
            db.executemany('INSERT OR IGNORE INTO report_links(report_id,source_report_id) VALUES (?,?)',
                           [(new_id, source_id) for source_id in all_ids])
            db.executemany('INSERT OR IGNORE INTO processed_report_ids(report_id) VALUES (?)',
                           [(source_id,) for source_id in ids])
    return '사무직' if status == 'pending_office' else '간부직'




def _attach_source_authors(db, rows):
    """Attach unique source-character names to each report for its footer."""
    results = [dict(row) for row in rows]
    if not results:
        return results
    ids = [r['report_id'] for r in results]
    marks = ','.join('?' for _ in ids)
    links = db.execute(f"""SELECT rl.report_id, src.author_name
        FROM report_links rl
        JOIN reports src ON src.report_id = rl.source_report_id
        WHERE rl.report_id IN ({marks})
        ORDER BY rl.source_report_id""", ids).fetchall()
    authors = {rid: [] for rid in ids}
    for link in links:
        names = authors[link['report_id']]
        if link['author_name'] not in names:
            names.append(link['author_name'])
    for r in results:
        r['source_authors'] = authors[r['report_id']]
    return results

def inbox(user_id, name):
    c = _character(user_id, name)
    with connect() as db:
        if c['job'] == '사무직':
            rows = db.execute("""SELECT * FROM reports WHERE (status='pending_office'
                OR (status='returned' AND report_type='official')) AND report_id NOT IN (SELECT report_id FROM processed_report_ids) ORDER BY report_id DESC""").fetchall()
        elif c['job'] == '간부직':
            rows = db.execute("SELECT * FROM reports WHERE status='pending_executive' ORDER BY report_id DESC").fetchall()
        elif c['job'] == '연구직':
            rows = db.execute("""SELECT * FROM reports WHERE status='returned'
                AND report_type='research' AND author_character_id=? ORDER BY report_id DESC""", (c['character_id'],)).fetchall()
        else:
            raise ValueError('이 직군은 확인할 보고함이 없습니다.')
        enriched = _attach_source_authors(db, rows)
    return c, enriched


def decide(user_id, name, report_id, approve, reason=''):
    c = _character(user_id, name)
    if c['job'] != '간부직':
        raise ValueError('간부직 캐릭터만 승인·반려할 수 있습니다.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        r = db.execute("SELECT * FROM reports WHERE report_id=? AND status='pending_executive'", (report_id,)).fetchone()
        if not r:
            raise ValueError('이미 처리되었거나 심사할 수 없는 보고서입니다.')
        db.execute('''UPDATE reports SET status=?, rejection_reason=?, reviewer_character_id=?,
            reviewed_at=CURRENT_TIMESTAMP WHERE report_id=?''',
            ('approved' if approve else 'returned', '' if approve else reason.strip(), c['character_id'], report_id))
    return dict(r)


def approved():
    with connect() as db:
        return _attach_source_authors(db, db.execute("SELECT * FROM reports WHERE status='approved' ORDER BY report_id DESC").fetchall())

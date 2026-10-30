"""Shop transactions; confirmations never mutate until the final button click."""
from services.characters import connect


def selected(guild_id, user_id):
    with connect() as db:
        row = db.execute('''SELECT c.character_id,c.name,p.gold FROM character_chat_selection s
          JOIN characters c ON c.character_id=s.character_id AND c.user_id=s.user_id
          JOIN character_progress p ON p.character_id=c.character_id
          WHERE s.guild_id=? AND s.user_id=?''',(str(guild_id),str(user_id))).fetchone()
        if not row: raise ValueError('먼저 /캐릭터선택으로 캐릭터를 선택해 주세요.')
        return dict(row)


def item(db, name):
    rows=db.execute('SELECT * FROM item_definitions WHERE name=? COLLATE NOCASE AND enabled=1',(name.strip(),)).fetchall()
    if not rows: raise ValueError('해당 이름의 활성 아이템을 찾을 수 없습니다.')
    if len(rows)>1: raise ValueError('같은 이름의 아이템이 여러 개입니다. 관리자에게 이름을 구분해 달라고 요청해 주세요.')
    return rows[0]


def register(name, quantity, price, visible):
    if quantity < -1 or quantity > 999999: raise ValueError('개수는 -1 또는 0~999999 사이여야 합니다.')
    if price < 0: raise ValueError('가격은 0 이상이어야 합니다.')
    if visible not in (0,1): raise ValueError('공개유무는 0 또는 1이어야 합니다.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        target=item(db,name)
        if quantity==-1:
            db.execute('DELETE FROM shop_products WHERE item_id=?',(target['item_id'],))
            return f"{target['name']} 상품을 목록에서 삭제했습니다."
        db.execute('''INSERT INTO shop_products(item_id,stock,price,visible) VALUES(?,?,?,?)
           ON CONFLICT(item_id) DO UPDATE SET stock=excluded.stock,price=excluded.price,visible=excluded.visible''',
           (target['item_id'],quantity,price,visible))
        return f"{target['name']} 등록 완료 · 재고 {'무한' if quantity==0 else str(quantity)} · {price:,}G · {'공개' if visible else '비공개'}"


def products():
    with connect() as db:
        return [dict(x) for x in db.execute('''SELECT d.name,p.stock,p.price FROM shop_products p
          JOIN item_definitions d ON d.item_id=p.item_id WHERE p.visible=1 AND d.enabled=1
          ORDER BY d.name''')]


def preview(guild_id,user_id,name,quantity,mode):
    if not 1<=quantity<=999999: raise ValueError('개수는 1~999999 사이여야 합니다.')
    char=selected(guild_id,user_id)
    with connect() as db:
        target=item(db,name)
        if mode=='buy':
            product=db.execute('SELECT * FROM shop_products WHERE item_id=?',(target['item_id'],)).fetchone()
            if not product: raise ValueError('상점에서 판매하지 않는 상품입니다.')
            if product['stock']<0 or (product['stock']>0 and product['stock']<quantity): raise ValueError('상점 재고가 부족합니다.')
            total=product['price']*quantity
            if char['gold']<total: raise ValueError('소지금이 부족합니다.')
            owned=db.execute('SELECT quantity FROM character_inventory WHERE character_id=? AND item_id=?',(char['character_id'],target['item_id'])).fetchone()
            held=owned['quantity'] if owned else 0
            if held+quantity>target['max_stack']: raise ValueError('최대 보유 수량을 초과합니다.')
            remain=char['gold']-total
        else:
            if target['sell_price'] is None: raise ValueError('판매 가격이 설정되지 않은 아이템입니다. Items 시트의 sell_price를 확인해 주세요.')
            owned=db.execute('SELECT quantity FROM character_inventory WHERE character_id=? AND item_id=?',(char['character_id'],target['item_id'])).fetchone()
            held=owned['quantity'] if owned else 0
            if held<quantity: raise ValueError(f'소지 수량이 부족합니다. 현재 {held}개')
            if db.execute('SELECT 1 FROM character_equipment WHERE character_id=? AND item_id=?',(char['character_id'],target['item_id'])).fetchone() and held-quantity<1:
                raise ValueError('장착 중인 장비는 마지막 1개를 판매할 수 없습니다. 먼저 장비를 해제해 주세요.')
            total=target['sell_price']*quantity
            remain=held-quantity
        return {'character':char['name'],'item_id':target['item_id'],'name':target['name'],'quantity':quantity,'total':total,'remaining':remain,'mode':mode}


def execute(guild_id,user_id,preview):
    mode=preview['mode']; quantity=preview['quantity']; item_id=preview['item_id']
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        char=db.execute('''SELECT c.character_id,c.name,p.gold FROM character_chat_selection s
          JOIN characters c ON c.character_id=s.character_id AND c.user_id=s.user_id
          JOIN character_progress p ON p.character_id=c.character_id
          WHERE s.guild_id=? AND s.user_id=?''',(str(guild_id),str(user_id))).fetchone()
        if not char or char['name']!=preview['character']:
            raise ValueError('선택된 캐릭터가 변경되었습니다. 명령어를 다시 실행해 주세요.')
        target=db.execute('SELECT * FROM item_definitions WHERE item_id=? AND enabled=1',(item_id,)).fetchone()
        if not target: raise ValueError('아이템이 비활성화되었습니다.')
        inv=db.execute('SELECT quantity FROM character_inventory WHERE character_id=? AND item_id=?',(char['character_id'],item_id)).fetchone()
        held=inv['quantity'] if inv else 0
        if mode=='buy':
            p=db.execute('SELECT * FROM shop_products WHERE item_id=?',(item_id,)).fetchone()
            if not p: raise ValueError('상품이 삭제되었습니다.')
            total=p['price']*quantity
            if total!=preview['total']: raise ValueError('상품 가격이 변경되었습니다. 다시 확인해 주세요.')
            if p['stock']<0 or (p['stock']>0 and p['stock']<quantity): raise ValueError('상품 재고가 부족합니다.')
            if char['gold']<total: raise ValueError('소지금이 부족합니다.')
            if held+quantity>target['max_stack']: raise ValueError('최대 보유 수량을 초과합니다.')
            db.execute('UPDATE character_progress SET gold=gold-? WHERE character_id=?',(total,char['character_id']))
            db.execute('''INSERT INTO character_inventory(character_id,item_id,quantity) VALUES(?,?,?)
              ON CONFLICT(character_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity''',(char['character_id'],item_id,quantity))
            if p['stock']>0: db.execute('UPDATE shop_products SET stock=? WHERE item_id=?',(-1 if p['stock']==quantity else p['stock']-quantity,item_id))
        else:
            if target['sell_price'] is None: raise ValueError('판매할 수 없는 아이템입니다.')
            total=target['sell_price']*quantity
            if total!=preview['total']: raise ValueError('판매 가격이 변경되었습니다. 다시 확인해 주세요.')
            if held<quantity: raise ValueError('아이템 수량이 부족합니다.')
            if db.execute('SELECT 1 FROM character_equipment WHERE character_id=? AND item_id=?',(char['character_id'],item_id)).fetchone() and held-quantity<1:
                raise ValueError('장착 중인 장비를 먼저 해제해 주세요.')
            db.execute('UPDATE character_inventory SET quantity=quantity-? WHERE character_id=? AND item_id=?',(quantity,char['character_id'],item_id))
            db.execute('UPDATE character_progress SET gold=gold+? WHERE character_id=?',(total,char['character_id']))
        return f"{char['name']}: {target['name']} {quantity}개 {'구매' if mode=='buy' else '판매'} 완료 · {total:,}G {'사용' if mode=='buy' else '획득'}"

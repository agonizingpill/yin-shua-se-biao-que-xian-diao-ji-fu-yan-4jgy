import os
from sqlalchemy import create_engine,text,inspect,event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
_url=os.environ['DATABASE_URL']
_isolation=None if _url.startswith('sqlite') else "READ COMMITTED"
_connect={'check_same_thread':False} if _url.startswith('sqlite') else {}
engine = create_engine(_url, pool_pre_ping=True,isolation_level=_isolation,connect_args=_connect)
if _url.startswith('sqlite'):
    @event.listens_for(engine,'connect')
    def _sqlite_pragmas(dbapi_conn,_):
        # 允许 worker 线程与 API 并发：WAL 读写不互斥，忙等避免瞬时锁报错
        cur=dbapi_conn.cursor()
        cur.execute('PRAGMA journal_mode=WAL');cur.execute('PRAGMA busy_timeout=30000');cur.close()
Session = sessionmaker(engine, expire_on_commit=False)
class Base(DeclarativeBase): pass
def db_session():
    with Session() as s:
        yield s

# 既有演示库的增量补列/放宽（只增不改不删数据，保留已签印原始证据）；新库由 create_all 直接建齐
_ADDITIVE_COLUMNS={
    'reference_sheets':[('state',"VARCHAR(20) NOT NULL DEFAULT 'active'")],
    'proof_sheets':[('model_version','INTEGER NOT NULL DEFAULT 1')],
}
def ensure_schema():
    insp=inspect(engine)
    with engine.begin() as conn:
        for table,columns in _ADDITIVE_COLUMNS.items():
            if table not in insp.get_table_names():continue
            existing={c['name'] for c in insp.get_columns(table)}
            for name,ddl in columns:
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
        # MySQL 旧库把停机单 sample_id 放宽为可空（修订复判停机无对应抽检）
        if engine.dialect.name=='mysql' and 'press_holds' in insp.get_table_names():
            col=[c for c in insp.get_columns('press_holds') if c['name']=='sample_id']
            if col and col[0]['nullable'] is False:
                conn.execute(text('ALTER TABLE press_holds MODIFY sample_id INTEGER NULL'))

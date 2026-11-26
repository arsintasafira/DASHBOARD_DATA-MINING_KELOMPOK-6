"""LANGKAH 2 - Membuat dashboard HTML dari file database DuckDB (hasil langkah 1).
Butuh di folder yang sama: 4 file .duckdb, template.html, dan 2 file .geojson.
Jalankan: python 2_buat_dashboard.py
Pasang dulu: pip install duckdb"""
import json, re, sys
from pathlib import Path
import duckdb

DATA = Path(__file__).parent
OUT = DATA / 'dashboard_bencana_bnpb.html'
DB_BNPB, DB_WILAYAH, DB_TWITTER = DATA / 'data_bnpb.duckdb', DATA / 'data_wilayah.duckdb', DATA / 'data_twitter.duckdb'
DB_HASIL = DATA / 'hasil_analisis.duckdb'
for f in (DB_BNPB, DB_WILAYAH, DB_TWITTER, DB_HASIL):
    if not f.exists(): sys.exit(f'File {f.name} belum ada. Jalankan dulu: python 1_buat_database.py')
K=40; X0=94; Y0=6.5
def pr(lon,lat): return (round((lon-X0)*K),round((Y0-lat)*K))
def rdp(pts,eps):
    if len(pts)<5: return pts
    keep=[False]*len(pts); keep[0]=keep[-1]=True; st=[(0,len(pts)-1)]
    while st:
        a,b=st.pop(); (x1,y1),(x2,y2)=pts[a],pts[b]; dx,dy=x2-x1,y2-y1; L=dx*dx+dy*dy; mx=-1; mi=-1
        for i in range(a+1,b):
            x,y=pts[i]
            if L==0: d=(x-x1)**2+(y-y1)**2
            else:
                t=max(0,min(1,((x-x1)*dx+(y-y1)*dy)/L)); d=(x-x1-t*dx)**2+(y-y1-t*dy)**2
            if d>mx: mx=d; mi=i
        if mx>eps*eps: keep[mi]=True; st+=[(a,mi),(mi,b)]
    return [p for p,k in zip(pts,keep) if k]
def area(r): return abs(sum(r[i][0]*r[i-1][1]-r[i-1][0]*r[i][1] for i in range(len(r))))/2
def path(geom,eps):
    polys=geom['coordinates'] if geom['type']=='MultiPolygon' else [geom['coordinates']]
    out=[];best=None
    for poly in polys:
        for ring in poly:
            p=[pr(*c[:2]) for c in ring]; q=[p[0]]
            for z in p[1:]:
                if z!=q[-1]: q.append(z)
            q=rdp(q,eps)
            if len(q)<4: continue
            a=area(q)
            if best is None or a>best[0]: best=(a,q)
            if a>=1.5: out.append(q)
    if not out and best: out=[best[1]]
    return ''.join('M'+'L'.join(f'{x} {y}' for x,y in r)+'Z' for r in out)

# ---------- 1. Baca hasil dari DuckDB ----------
con = duckdb.connect(str(DB_HASIL), read_only=True)
for alias, f in [('db_bnpb', DB_BNPB), ('db_wilayah', DB_WILAYAH), ('db_twitter', DB_TWITTER)]:
    con.execute(f"ATTACH '{f.as_posix()}' AS {alias} (READ_ONLY)")
types = [r[0] for r in con.execute('SELECT jenis FROM db_bnpb.kejadian GROUP BY jenis ORDER BY COUNT(*) DESC, jenis').fetchall()]
rows = con.execute("""SELECT bulan_idx, jenis, kode_kab, meninggal, hilang, terluka, rumah_rusak, rumah_terendam, fasum_rusak
                      FROM db_bnpb.kejadian ORDER BY tgl, kode_kab""").fetchall()
ev = [[int(r[0]), types.index(r[1]), int(r[2])] + [int(x) for x in r[3:]] for r in rows]
months = [f'{2025 + i // 12}-{i % 12 + 1:02d}' for i in range(max(r[0] for r in ev) + 1)]
reg, prov, pp = {}, {}, {}
for kode, kp, pv, nama, pop, luas in con.execute('SELECT kode, kode_prov, provinsi, nama, penduduk, luas FROM db_wilayah.wilayah').fetchall():
    reg[kode] = [nama.replace('Kabupaten ', 'Kab. '), kp, int(pop), luas]
    prov[kp] = pv
    pp[kp] = pp.get(kp, 0) + int(pop)
tw_rows = [[types.index(r[1]), int(r[0]), int(r[2])] for r in con.execute('SELECT kode_kab, jenis, jumlah FROM db_twitter.twitter').fetchall() if r[1] in types]
# tabel harga beras (dibuat langkah 1 bila bebek_sp2kp_1.duckdb ada); kalau tidak ada, tab harga disembunyikan
hv_rows, hm_rows, kor_rows = [], [], []
if con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'bencana_vs_harga'").fetchone()[0]:
    hv_rows = con.execute('SELECT provinsi, kejadian, meninggal, rumah_rusak, dominasi_bencana, harga_rata, perubahan_pct, jenis_acuan, outlier FROM bencana_vs_harga').fetchall()
    hm_rows = con.execute('SELECT provinsi, bulan_idx, harga FROM harga_bulanan').fetchall()
    kor_rows = con.execute('SELECT subset, hubungan, metode, n, r, p_value FROM korelasi').fetchall()
con.close()

# ---------- 2. Peta (GeoJSON) & penyusunan dashboard ----------
norm=lambda x:re.sub(r'[^a-z0-9]','',x.lower())
csvk={(norm(prov[v[1]]),norm(v[0].replace('Kab. ',''))):c for c,v in reg.items()}
provcode={norm(n):c for c,n in prov.items()}
g2=json.load(open(DATA/'indonesia-38-prov-kabupaten-kota.geojson',encoding='utf-8'));g1=json.load(open(DATA/'indonesia-38-provinces.geojson',encoding='utf-8'))
kab={};nomatch=[]
for f in g2['features']:
    p=f['properties']; k=csvk.get((norm(p['PROVINSI']),norm(re.sub('^Kabupaten ','',p['KAB_KOTA']))))
    if k is None: k=int(p['KODE_KK'].replace('.','')); nomatch.append((p['KODE_KK'],p['KAB_KOTA'],p['PROVINSI']))
    kab[k]=path(f['geometry'],0.6)
    if k not in reg: reg[k]=[p['KAB_KOTA'],k//100,0,0]
provp={}
for f in g1['features']:
    p=f['properties']; k=provcode.get(norm(p['PROVINSI']),int(p['KODE_PROV'])); provp[k]=path(f['geometry'],1.0); prov[k]=p['PROVINSI']
print('poligon kab tanpa padanan nama di CSV:',len(nomatch),nomatch[:10])
used=set(r[2] for r in ev)
miss=sorted(used-set(kab)); 
print('kejadian dipakai:',len(ev))
print('kode kab di data tapi tidak ada di peta:',len(miss),miss[:15],'=>',sum(1 for r in ev if r[2] in miss),'kejadian')
print('kode kab di data tapi tidak ada di CSV:',sorted(used-set(reg))[:15])
print('poligon kab:',len(kab),'prov:',len(provp))
# ---------- 2b. Data harga beras per provinsi (kunci = kode provinsi) ----------
hb, nbln = {}, (max(r[1] for r in hm_rows) + 1 if hm_rows else 0)
rd = lambda v, n=1: None if v is None else round(float(v), n)
for pv, kej, men, rr, dom, hr, pc, ja, out in hv_rows:
    kp = provcode.get(norm(pv))
    if kp is None: print('provinsi harga tanpa kode:', pv); continue
    hb[str(kp)] = [int(kej), int(men), int(rr), dom, rd(hr, 0), rd(pc, 2), ja, int(bool(out)), [None] * nbln]
for pv, bi, hg in hm_rows:
    kp = provcode.get(norm(pv))
    if kp is not None and str(kp) in hb: hb[str(kp)][8][int(bi)] = rd(hg, 0)
kor = [[a, b, c, int(n), rd(r, 3), rd(pv, 3)] for a, b, c, n, r, pv in kor_rows]
print('provinsi dengan data harga:', sum(1 for v in hb.values() if v[4] is not None), 'dari', len(hb))

D=dict(hb=hb,hmonths=[f'2026-{i+1:02d}' for i in range(nbln)],kor=kor,types=types,months=months,reg={str(k):v for k,v in reg.items()},prov={str(k):v for k,v in prov.items()},provPop={str(k):v for k,v in pp.items()},
       ev=ev,tw=tw_rows,kab={str(k):v for k,v in kab.items()},provPath={str(k):v for k,v in provp.items()},vb=f'-10 -10 {round((141.2-X0)*K)+20} {round((Y0+11.2)*K)+20}')
js=json.dumps(D,ensure_ascii=False,separators=(',',':'))
html=open(Path(__file__).parent/'template.html',encoding='utf-8').read().replace('/*DATA*/null',js)
OUT.write_text(html,encoding='utf-8')
print('ukuran HTML:',round(len(html)/1024),'KB')
import webbrowser; webbrowser.open(OUT.as_uri())  # buka dashboard otomatis di browser

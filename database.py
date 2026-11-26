"""LANGKAH 1 - Membuat file database DuckDB dari data mentah.
Taruh file ini di folder yang sama dengan data (Excel BNPB, CSV wilayah, Excel Twitter, bebek_sp2kp_1.duckdb).
Jalankan: python 1_buat_database.py
Hasil: data_bnpb.duckdb, data_wilayah.duckdb, data_twitter.duckdb, hasil_analisis.duckdb
(hasil_analisis.duckdb kini juga berisi tabel harga beras: harga_bulanan, bencana_vs_harga, korelasi)
Pasang dulu: pip install duckdb pandas openpyxl numpy"""
import re
from pathlib import Path
import duckdb, numpy as np, pandas as pd

DATA = Path(__file__).parent
DB_BNPB, DB_WILAYAH, DB_TWITTER = DATA / 'data_bnpb.duckdb', DATA / 'data_wilayah.duckdb', DATA / 'data_twitter.duckdb'
DB_HASIL = DATA / 'hasil_analisis.duckdb'

# ---------- 1. Muat data mentah ke DuckDB ----------
frames = [pd.read_excel(f, header=1) for f in sorted(DATA.rglob('*.xlsx')) if f.name.startswith('Data Bencana')]
raw = pd.concat(frames, ignore_index=True)
raw = raw[pd.to_numeric(raw['No.'], errors='coerce').notna()]
num = lambda c: pd.to_numeric(raw[c], errors='coerce').fillna(0).astype(int)
df = pd.DataFrame({
    'jenis': raw['Kejadian'].astype(str).str.strip(),
    'tgl': pd.to_datetime(raw['Tanggal Kejadian'], errors='coerce'),
    'kode_kab': pd.to_numeric(raw['ID Kabupaten'], errors='coerce').astype('Int64'),
    'meninggal': num('Meninggal'), 'hilang': num('Hilang'), 'terluka': num('Terluka'),
    'rumah_rusak': num('Rumah Rusak'), 'rumah_terendam': num('Rumah Terendam'), 'fasum_rusak': num('Fasum Rusak'),
})
con = duckdb.connect(str(DB_HASIL))
for alias, f in [('db_bnpb', DB_BNPB), ('db_wilayah', DB_WILAYAH), ('db_twitter', DB_TWITTER)]:
    con.execute(f"ATTACH '{f.as_posix()}' AS {alias}")
con.register('df_mentah', df)
con.execute('CREATE OR REPLACE TABLE db_bnpb.kejadian_mentah AS SELECT * FROM df_mentah')
csv_path = (DATA / 'kabupaten_kota_indonesia.csv').as_posix()
con.execute(f"""CREATE OR REPLACE TABLE db_wilayah.wilayah AS
  SELECT CAST(REPLACE("Kode Kemendagri", '.', '') AS INTEGER) AS kode,
         CAST("Kode Provinsi" AS INTEGER) AS kode_prov, "Provinsi" AS provinsi,
         "Nama Kabupaten/Kota" AS nama,
         COALESCE(TRY_CAST("Penduduk" AS DOUBLE), 0) AS penduduk,
         COALESCE(TRY_CAST("Luas (km²)" AS DOUBLE), 0) AS luas
  FROM read_csv('{csv_path}', header = true, all_varchar = true)""")

# ---------- 2. Bersihkan & gabungkan dengan SQL ----------
con.execute("""CREATE OR REPLACE TABLE db_bnpb.kejadian AS
  SELECT jenis, tgl, kode_kab,
         (year(tgl) - 2025) * 12 + month(tgl) - 1 AS bulan_idx,
         meninggal, hilang, terluka, rumah_rusak, rumah_terendam, fasum_rusak
  FROM db_bnpb.kejadian_mentah
  WHERE kode_kab IS NOT NULL AND tgl IS NOT NULL""")
con.execute("""CREATE OR REPLACE TABLE db_bnpb.rekap_jenis AS
  SELECT jenis, COUNT(*) AS kejadian, SUM(meninggal) AS meninggal, SUM(hilang) AS hilang, SUM(terluka) AS terluka,
         SUM(rumah_rusak) AS rumah_rusak, SUM(rumah_terendam) AS rumah_terendam, SUM(fasum_rusak) AS fasum_rusak
  FROM db_bnpb.kejadian GROUP BY jenis ORDER BY kejadian DESC""")
con.execute("""CREATE OR REPLACE TABLE rekap_provinsi AS
  SELECT w.provinsi, COUNT(*) AS kejadian, SUM(k.meninggal) AS meninggal, SUM(k.hilang) AS hilang, SUM(k.terluka) AS terluka,
         SUM(k.rumah_rusak) AS rumah_rusak, SUM(k.rumah_terendam) AS rumah_terendam, SUM(k.fasum_rusak) AS fasum_rusak
  FROM db_bnpb.kejadian k JOIN db_wilayah.wilayah w ON k.kode_kab = w.kode GROUP BY w.provinsi ORDER BY kejadian DESC""")
con.execute("""CREATE OR REPLACE TABLE db_bnpb.rekap_bulan AS
  SELECT bulan_idx, jenis, COUNT(*) AS kejadian FROM db_bnpb.kejadian GROUP BY bulan_idx, jenis ORDER BY bulan_idx, jenis""")

n_all = con.execute('SELECT COUNT(*) FROM db_bnpb.kejadian_mentah').fetchone()[0]
n_ok = con.execute('SELECT COUNT(*) FROM db_bnpb.kejadian').fetchone()[0]
n_noreg = con.execute('SELECT COUNT(*) FROM db_bnpb.kejadian k LEFT JOIN db_wilayah.wilayah w ON k.kode_kab = w.kode WHERE w.kode IS NULL').fetchone()[0]
print(f'baris mentah: {n_all} | lolos pembersihan: {n_ok} | kode kab tidak ada di CSV: {n_noreg}')
print(con.execute('SELECT * FROM db_bnpb.rekap_jenis').df().to_string(index=False))

# ---------- 3. Dataset postingan Twitter (per kabupaten/kota & jenis bencana) ----------
norm_k = lambda x: re.sub(r'[^a-z0-9]', '', re.sub('administrasi', '', str(x).lower().replace('kep.', 'kepulauan')))
alias = {'dkijakarta': 'daerahkhususibukotajakarta', 'diyogyakarta': 'daerahistimewayogyakarta'}
def norm_p(x):
    x = re.sub(r'[^a-z0-9]', '', str(x).lower()); return alias.get(x, x)
wk = {(norm_p(pv), norm_k(nama)): kode for kode, pv, nama in con.execute('SELECT kode, provinsi, nama FROM db_wilayah.wilayah').fetchall()}
tw_file = next(f for f in sorted(DATA.rglob('*.xlsx')) if not f.name.startswith('Data Bencana'))
tw = pd.read_excel(tw_file); tw.columns = [str(c).strip() for c in tw.columns]
tl = tw.melt(id_vars=['Kab/ Kota', 'Provinsi'], var_name='jenis', value_name='jumlah')
tl['jumlah'] = pd.to_numeric(tl['jumlah'], errors='coerce'); tl = tl[tl['jumlah'] > 0].copy()
tl['kode_kab'] = [wk.get((norm_p(p), norm_k(k))) for k, p in zip(tl['Kab/ Kota'], tl['Provinsi'])]
print('postingan Twitter: sel terisi', len(tl), '| nama wilayah tak cocok:', int(tl['kode_kab'].isna().sum()))
tl = tl.dropna(subset=['kode_kab'])
df_tw = pd.DataFrame({'kode_kab': tl['kode_kab'].astype(int), 'jenis': tl['jenis'].astype(str).str.strip(), 'jumlah': tl['jumlah'].astype(int)})
con.register('df_tw', df_tw)
con.execute('CREATE OR REPLACE TABLE db_twitter.twitter AS SELECT kode_kab, jenis, jumlah FROM df_tw')
con.execute("""CREATE OR REPLACE TABLE bandingkan AS
  SELECT COALESCE(a.kode_kab, b.kode_kab) AS kode_kab, COALESCE(a.jenis, b.jenis) AS jenis,
         COALESCE(a.kejadian, 0) AS kejadian_bnpb, COALESCE(b.jumlah, 0) AS postingan_twitter
  FROM (SELECT kode_kab, jenis, COUNT(*) AS kejadian FROM db_bnpb.kejadian GROUP BY kode_kab, jenis) a
  FULL OUTER JOIN db_twitter.twitter b ON a.kode_kab = b.kode_kab AND a.jenis = b.jenis""")
print(con.execute("""SELECT jenis, SUM(kejadian_bnpb) AS kejadian_bnpb, SUM(postingan_twitter) AS postingan_twitter,
        SUM(CASE WHEN kejadian_bnpb > 0 AND postingan_twitter > 0 THEN 1 ELSE 0 END) AS wilayah_beririsan
      FROM bandingkan GROUP BY jenis ORDER BY kejadian_bnpb DESC""").df().to_string(index=False))
# ---------- 4. Harga beras SP2KP (Jan-Agu 2026) vs bencana (2025) per provinsi ----------
cands = sorted(DATA.glob('bebek_sp2kp*.duckdb'))
if not cands:
    print('\n[LEWATI] bebek_sp2kp_1.duckdb tidak ada di folder ini, bagian harga beras tidak dibuat.')
else:
    DB_HARGA = cands[0]
    con.execute(f"ATTACH '{DB_HARGA.as_posix()}' AS db_harga (READ_ONLY)")
    # nama provinsi SP2KP -> nama provinsi di data wilayah/BNPB
    ALIAS_PROV = {'DKI Jakarta': 'Daerah Khusus Ibukota Jakarta'}
    h = con.execute("""SELECT bulan_num, provinsi, nama_pasar, harga FROM db_harga.harga_pasar_bulanan
                       WHERE tahun = 2026 AND jenis_beras = 'Beras Medium'""").df()
    n_baris = len(h)
    h = h[h['harga'].notna() & (h['harga'] > 0)].copy()
    h['provinsi'] = h['provinsi'].str.strip().replace(ALIAS_PROV)
    h['bulan_idx'] = h['bulan_num'] - 1                           # 0 = Jan 2026
    # Harga provinsi = MEDIAN harga antar pasar per bulan (tahan nilai ekstrem, mis. Pasar Ilaga Papua Tengah Rp45.000)
    ref = h.groupby(['provinsi', 'bulan_idx'], as_index=False)['harga'].median().assign(jenis_acuan='Medium')
    # Outlier otomatis: rata-rata harga provinsi di luar 0,7x - 1,5x median antar provinsi -> DITANDAI, bukan dihapus
    rata = ref.groupby('provinsi')['harga'].mean(); med = rata.median()
    out_set = set(rata[(rata < 0.7 * med) | (rata > 1.5 * med)].index)
    ref['outlier'] = ref['provinsi'].isin(out_set)
    print(f"\nHarga SP2KP: {n_baris} baris pasar (Beras Medium 2026), {h['nama_pasar'].nunique()} pasar | provinsi berharga: {ref.provinsi.nunique()} | outlier ditandai: {sorted(out_set) or 'tidak ada'}")
    con.register('df_ref', ref)
    con.execute('CREATE OR REPLACE TABLE harga_bulanan AS SELECT * FROM df_ref ORDER BY provinsi, bulan_idx')
    con.execute("""CREATE OR REPLACE TABLE dominasi_provinsi AS
      SELECT provinsi, jenis AS dominasi_bencana FROM (
        SELECT w.provinsi, k.jenis, COUNT(*) c, ROW_NUMBER() OVER (PARTITION BY w.provinsi ORDER BY COUNT(*) DESC, k.jenis) rn
        FROM db_bnpb.kejadian k JOIN db_wilayah.wilayah w ON k.kode_kab = w.kode GROUP BY w.provinsi, k.jenis) WHERE rn = 1""")
    con.execute("""CREATE OR REPLACE TABLE bencana_vs_harga AS
      WITH hp AS (
        SELECT provinsi, ANY_VALUE(jenis_acuan) AS jenis_acuan, BOOL_OR(outlier) AS outlier, COUNT(*) AS n_bulan,
               AVG(harga) AS harga_rata, ARG_MIN(harga, bulan_idx) AS harga_awal, ARG_MAX(harga, bulan_idx) AS harga_akhir
        FROM harga_bulanan GROUP BY provinsi)
      SELECT r.provinsi, r.kejadian, r.meninggal, r.hilang, r.terluka, r.rumah_rusak, r.rumah_terendam, d.dominasi_bencana,
             hp.harga_rata, hp.harga_awal, hp.harga_akhir, (hp.harga_akhir / hp.harga_awal - 1) * 100 AS perubahan_pct,
             hp.n_bulan, hp.jenis_acuan, COALESCE(hp.outlier, FALSE) AS outlier
      FROM rekap_provinsi r LEFT JOIN dominasi_provinsi d USING (provinsi) LEFT JOIN hp USING (provinsi)
      ORDER BY r.kejadian DESC""")
    bv = con.execute('SELECT * FROM bencana_vs_harga').df()

    # Uji korelasi (Pearson & Spearman) + p-value permutasi (tanpa scipy)
    rng = np.random.default_rng(42)
    def uji(x, y, metode):
        x, y = pd.Series(x).astype(float), pd.Series(y).astype(float)
        if metode == 'Spearman': x, y = x.rank(), y.rank()
        r = float(np.corrcoef(x, y)[0, 1]); xv, yv = x.values, y.values
        perm = [np.corrcoef(xv, rng.permutation(yv))[0, 1] for _ in range(5000)]
        return r, float((np.abs(perm) >= abs(r)).mean())
    hasil = []
    ok = bv.dropna(subset=['harga_rata'])
    sub_tanpa = 'Tanpa outlier (' + ', '.join(sorted(out_set)) + ')' if out_set else 'Semua provinsi'
    subs = [('Semua provinsi', ok)] + ([(sub_tanpa, ok[~ok['outlier']])] if out_set else [])
    for sub, d in subs:
        for met in ('Pearson', 'Spearman'):
            r, p = uji(d['kejadian'], d['harga_rata'], met)
            hasil.append((sub, 'Jumlah bencana 2025 vs harga rata-rata 2026', met, len(d), r, p))
    d = ok[~ok['outlier']].dropna(subset=['perubahan_pct'])
    r, p = uji(d['kejadian'], d['perubahan_pct'], 'Spearman')
    hasil.append((sub_tanpa, 'Jumlah bencana 2025 vs perubahan harga Jan-Agu 2026', 'Spearman', len(d), r, p))
    kor = pd.DataFrame(hasil, columns=['subset', 'hubungan', 'metode', 'n', 'r', 'p_value'])
    con.register('df_kor', kor)
    con.execute('CREATE OR REPLACE TABLE korelasi AS SELECT * FROM df_kor')
    print('\nProvinsi bencana terbanyak:', bv.iloc[0]['provinsi'], int(bv.iloc[0]['kejadian']))
    t = ok.sort_values('harga_rata', ascending=False).iloc[0]; print('Provinsi harga tertinggi:', t['provinsi'], round(t['harga_rata']))
    print('BNPB tanpa data harga:', sorted(bv[bv['harga_rata'].isna()]['provinsi']))
    print(kor.round(3).to_string(index=False))

con.close()
print('\nSelesai. File database dibuat:')
for f in (DB_BNPB, DB_WILAYAH, DB_TWITTER, DB_HASIL): print(' -', f.name)
print('Langkah berikutnya: python 2_buat_dashboard.py')
